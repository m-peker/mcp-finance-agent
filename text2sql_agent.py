"""
Finans Asistanı — Çok Ajanlı Text-to-SQL Motoru
================================================
Doğal dildeki finansal soruları SQL sorgularına çevirir, veritabanında
çalıştırır ve sonuçları anlaşılır bir dille yanıtlar. İsteğe bağlı
olarak interaktif Plotly grafikleri de üretir.

Mimari (LangGraph state machine):
    guardrails_agent
        ├── kapsam dışı → END
        └── kapsam içi → sql_agent → execute_sql
                                         ├── hata → error_agent → execute_sql (tekrar, max 3×)
                                         └── başarı → analysis_agent → decide_graph_need
                                                                            ├── grafik yok → END
                                                                            └── grafik var → viz_agent → END

Kullanım:
    from text2sql_agent import process_question_stream
    async for event in process_question_stream("Bu ayki toplam giderim ne kadar?"):
        ...
"""

import os
import sqlite3
from typing import TypedDict
from langgraph.graph import StateGraph, END
from openai import OpenAI
import json
import pandas as pd

# ── İstemci ve Yapılandırma ────────────────────────────────────────────────────

# OpenAI istemcisini başlat; API anahtarı .env dosyasındaki OPENAI_API_KEY'den okunur
client  = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
DB_PATH = "finance.db"

# ── Veritabanı Şema Tanımı ─────────────────────────────────────────────────────
# SQL ajanına veritabanı yapısını tanıtmak için prompt'a eklenen metin bloğu.
# Yeni tablo veya kolon eklendiğinde burası da güncellenmelidir.

SCHEMA_INFO = """
Finans Yönetim Sistemi Veritabanı — 2024–2025 dönemi sentetik verisi:

1. accounts (Hesaplar)
   - account_id   (INTEGER): Benzersiz hesap kimliği
   - account_name (TEXT):    Hesap adı (ör. "Vadesiz Hesap", "Kredi Kartı")
   - account_type (TEXT):    Hesap türü — checking | savings | credit | cash
   - balance      (REAL):    Güncel bakiye
   - currency     (TEXT):    Para birimi (TRY, USD, EUR)

2. categories (Kategoriler)
   - category_id   (INTEGER): Benzersiz kategori kimliği
   - category_name (TEXT):    Kategori adı (ör. "Market", "Maaş", "Kira")
   - category_type (TEXT):    income (gelir) veya expense (gider)

3. transactions (İşlemler)
   - transaction_id   (INTEGER): Benzersiz işlem kimliği
   - transaction_date (TEXT):    İşlem tarihi — YYYY-MM-DD
   - amount           (REAL):    İşlem tutarı
   - transaction_type (TEXT):    income | expense | transfer
   - category_id      (INTEGER): FK → categories(category_id)
   - account_id       (INTEGER): FK → accounts(account_id)
   - description      (TEXT):    İşlem açıklaması
   - status           (TEXT):    completed | pending | cancelled

4. budgets (Bütçeler)
   - budget_id    (INTEGER): Benzersiz bütçe kimliği
   - category_id  (INTEGER): FK → categories(category_id)
   - month        (INTEGER): Ay (1–12)
   - year         (INTEGER): Yıl (2024 veya 2025)
   - limit_amount (REAL):    Aylık bütçe limiti
   - spent_amount (REAL):    Gerçekleşen harcama tutarı

5. invoices (Faturalar)
   - invoice_id     (INTEGER): Benzersiz fatura kimliği
   - vendor_name    (TEXT):    Tedarikçi / hizmet adı
   - invoice_date   (TEXT):    Fatura tarihi — YYYY-MM-DD
   - due_date       (TEXT):    Son ödeme tarihi — YYYY-MM-DD
   - amount         (REAL):    Fatura tutarı
   - status         (TEXT):    paid | pending | overdue
   - category_id    (INTEGER): FK → categories(category_id)
   - transaction_id (INTEGER): FK → transactions(transaction_id) — NULL olabilir

Not: Türkçe metin değerleri (kategori adları, açıklamalar vb.) veritabanında
     olduğu gibi saklanır; sorgularda büyük/küçük harf duyarlıdır.

Tarih Uyarısı (KRİTİK):
     budgets tablosundaki month (INTEGER: 1–12) kolonunu tarih stringine
     donusturmek icin MUTLAKA printf kullan:
         DOGRU:   printf('%04d-%02d', year, month)  --> '2024-01'
         YANLIS:  year || '-' || month || '-01'     --> '2024-1-01' (SQLite bunu NULL okur!)
"""

# ── Durum Tanımı (AgentState) ──────────────────────────────────────────────────
# LangGraph'in adımlar arasında ilettiği paylaşımlı veri yapısı.
# Her ajan bu state üzerinden bilgi okur ve günceller.

class AgentState(TypedDict):
    question:         str    # Kullanıcının doğal dildeki sorusu
    sql_query:        str    # Üretilen (veya düzeltilmiş) SQL sorgusu
    query_result:     str    # Sorgu sonuçları, JSON formatında
    final_answer:     str    # Kullanıcıya sunulacak nihai metin yanıtı
    error:            str    # Hata mesajı — boşsa hata yok
    iteration:        int    # Hata kurtarma döngüsünde kaçıncı denemede olunduğu
    needs_graph:      bool   # Görselleştirme üretilmeli mi?
    graph_type:       str    # Grafik türü: bar | line | pie | scatter
    graph_json:       str    # Plotly figürünün Chainlit'e iletilen JSON çıktısı
    is_in_scope:      bool   # Soru finans verisiyle ilgili mi?
    # ── Sanity Check (Sonuç Akıl Yürütme) ─────────────────────────────────
    sanity_passed:    bool   # Sonuçlar mantıklı mı? False → SQL yeniden üretilir
    sanity_issue:     str    # Tespit edilen sorun — sql_agent'e feedback olarak verilir
    sanity_retried:   bool   # Sanity nedeniyle SQL zaten yeniden üretildi mi? (döngü koruması)

# ── Ajan Yapılandırmaları ──────────────────────────────────────────────────────
# Her ajanın rolü ve temel sistem mesajı burada merkezi olarak tanımlanır.
# Model seçimi ve sıcaklık gibi parametreler her fonksiyon içinde ayrıca ayarlanır.

AGENT_CONFIGS = {
    "guardrails_agent": {
        "role": "Kapsam ve Güvenlik Denetçisi",
        "system_prompt": (
            "Sen katı bir kapsam filtresisin. Kullanıcı sorularının "
            "finans veri analizi için uygun olup olmadığını veya "
            "bir selamlama içerip içermediğini belirlersin."
        ),
    },
    "sql_agent": {
        "role": "SQL Uzmanı",
        "system_prompt": (
            "Sen finans veritabanlarında uzmanlaşmış kıdemli bir SQL geliştiricisisin. "
            "Yalnızca geçerli SQLite sorguları üret; biçimlendirme veya açıklama ekleme."
        ),
    },
    "analysis_agent": {
        "role": "Finansal Analist",
        "system_prompt": (
            "Sen veritabanı sorgu sonuçlarını anlaşılır, sade bir dille yorumlayan "
            "yardımsever bir finansal analistsin. Sayıları net biçimde sun, "
            "kısa ve odaklı kal."
        ),
    },
    "viz_agent": {
        "role": "Görselleştirme Uzmanı",
        "system_prompt": (
            "Sen bir veri görselleştirme uzmanısın. "
            "Markdown veya açıklama içermeyen, doğrudan çalışabilir Plotly kodu üret."
        ),
    },
    "sanity_check_agent": {
        "role": "Sonuç Akıl Yürütücüsü",
        "system_prompt": (
            "Sen bir finans veri kalite uzmanısın. SQL sorgu sonuçlarının "
            "soru ile tutarlı ve gerçekçi olup olmadığını değerlendirirsin. "
            "Yanıtını yalnızca JSON formatında ver."
        ),
    },
    "sql_validator_agent": {
        "role": "SQL Kalite Denetçisi",
        "system_prompt": (
            "Sen bir SQL kalite denetçisisin. Fan-out hatalarını tespit eder, "
            "gerektiğinde sorguyu CTE yapısına dönüştürürsün. "
            "Yalnızca geçerli SQLite sözdizimi üret."
        ),
    },
    "error_agent": {
        "role": "Hata Kurtarma Uzmanı",
        "system_prompt": (
            "Sen hatalı SQL sorgularını şema bilgisini ve hata mesajını kullanarak "
            "tespit edip düzelten bir veritabanı uzmanısın."
        ),
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# AJAN FONKSİYONLARI
# Her fonksiyon AgentState alır, ilgili alanı günceller ve state'i geri döner.
# ══════════════════════════════════════════════════════════════════════════════

def guardrails_agent(state: AgentState) -> AgentState:
    """
    Soruyu üç sınıfa ayırır: finans kapsamında, kapsam dışı veya selamlama.

    - Selamlama ise → final_answer'ı karşılama mesajıyla doldur, SQL'e geçme.
    - Kapsam dışı ise → final_answer'ı yönlendirme mesajıyla doldur, SQL'e geçme.
    - Kapsam içi ise → is_in_scope=True, sql_agent devreye girer.
    """
    question = state["question"]

    prompt = f"""Sen bir finans veritabanı asistanı için kapsam denetçisisin.
Kullanıcının sorusunun finans verisiyle ilgili olup olmadığını,
bir selamlama mı yoksa kapsam dışı bir soru mu olduğunu belirle.

Veritabanında şunlar bulunmaktadır (2024–2025 dönemi):
- Banka hesapları ve bakiyeler
- Gelir / gider işlemleri
- Kategori bazlı harcama kayıtları (market, kira, maaş vb.)
- Aylık bütçe limitleri ve gerçekleşen tutarlar
- Faturalar ve ödeme durumları

SELAMLAMA örnekleri:
- "Merhaba", "Selam", "Nasılsın?", "Hi", "Hello"

KAPSAM İÇİ örnekler:
- "Bu ayki toplam harcamam ne kadar?"
- "Bütçeyi aştığım kategoriler hangileri?"
- "Ödenmemiş faturalarım var mı?"
- "En yüksek harcama hangi kategoride?"
- "Kredi kartı borcum ne kadar?"

KAPSAM DIŞI örnekler:
- Yatırım tavsiyesi ("Bitcoin almalı mıyım?")
- Genel bilgi soruları ("Türkiye'nin başkenti neresi?")
- Hava durumu, spor sonuçları, politika vb.

Kullanıcı sorusu: {question}

JSON formatında yanıt ver:
{{
    "is_in_scope": true/false,
    "is_greeting": true/false,
    "reason": "kısa açıklama"
}}

Finans verisiyle ilişkilendirilebilecek belirsiz sorular kapsam içi sayılmalıdır."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": AGENT_CONFIGS["guardrails_agent"]["system_prompt"]},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    result               = json.loads(response.choices[0].message.content)
    state["is_in_scope"] = result.get("is_in_scope", False)
    is_greeting          = result.get("is_greeting", False)

    if is_greeting:
        state["final_answer"] = (
            "Merhaba! Ben Finans Asistanınım. 💰\n\n"
            "2024–2025 dönemine ait finansal verileriniz hakkında soru sorabilirsiniz:\n"
            "- Hesap bakiyeleri ve işlem geçmişi\n"
            "- Kategori bazlı gelir/gider analizi\n"
            "- Bütçe takibi ve aşımlar\n"
            "- Fatura ve ödeme durumları\n\n"
            "Nasıl yardımcı olabilirim?"
        )
        return state

    if not state["is_in_scope"]:
        state["final_answer"] = (
            "Üzgünüm, bu soru finans verilerimin kapsamı dışında. "
            "Aşağıdaki konularda yardımcı olabilirim:\n\n"
            "- 💳 Hesaplar ve bakiyeler\n"
            "- 📊 Gelir / gider analizi\n"
            "- 🎯 Bütçe takibi\n"
            "- 🧾 Fatura ve ödeme durumları\n\n"
            "Lütfen finans verileriyle ilgili bir soru sorun."
        )

    return state


def sql_agent(state: AgentState) -> AgentState:
    """
    Kullanıcının sorusunu geçerli bir SQLite sorgusuna çevirir.
    Şema bilgisi prompt'a dahil edilerek doğru tablo/kolon kullanımı sağlanır.
    """
    question  = state["question"]
    iteration = state.get("iteration", 0)

    # Sanity check'ten gelen feedback varsa prompt'a ekle
    # Bu sayede sql_agent önceki hatanın ne olduğunu bilerek yeniden yazar
    sanity_feedback = state.get("sanity_issue", "")
    feedback_section = (
        f"\nÖNCEKİ DENEME HATASI (bu hatayı tekrarlama):\n{sanity_feedback}\n"
        if sanity_feedback else ""
    )

    prompt = f"""Aşağıdaki soruyu geçerli bir SQLite sorgusuna çevir.

{SCHEMA_INFO}
{feedback_section}
Soru: {question}

Kurallar:
1. Yalnızca şemada tanımlı tablo ve kolonları kullan.
2. Birden fazla tablo gerekiyorsa uygun JOIN ifadelerini kullan.
3. Yalnızca SQL sorgusunu döndür; açıklama veya markdown ekleme.
4. Soru birden fazla alt soru içeriyorsa sorguları noktalı virgülle ayır.
5. COUNT, SUM, AVG gibi toplama fonksiyonlarını uygun yerlerde kullan.
6. Çok satır dönebilecek sorgulara varsayılan LIMIT 10 ekle (kullanıcı belirtmemişse).
7. Tarih karşılaştırmalarında ISO formatı kullan (ör. '2024-01-01').
8. Türkçe metin değerleri büyük/küçük harf duyarlıdır; veritabanındaki yazımı koru.
9. KRİTİK — Kartezyen çarpım (fan-out) hatası: Birden fazla tablodan SUM, COUNT veya AVG
   hesaplarken HER agregasyonu ayrı bir CTE (WITH bloğu) içinde hesapla, sonunda birleştir.
   YANLIŞ: JOIN transactions JOIN invoices → SUM(amount), COUNT(invoice_id)
            (satırlar çarpışır, sonuçlar şişer)
   DOĞRU:  WITH tx  AS (SELECT category_id, SUM(amount) FROM transactions GROUP BY category_id)
            WITH inv AS (SELECT category_id, COUNT(*)   FROM invoices    GROUP BY category_id)
            Son sorguda bu CTE'leri JOIN'le.
10. Kategori adı gösterimi: Sorguda category_id kullanıyorsan sonuç setinde her zaman
    categories tablosunu JOIN ederek category_name'i de getir; ham ID döndürme.
11. Harcama toplamlarını (total_spent) budgets tablosundan değil, transactions tablosundan
    hesapla; budgets yalnızca limit karşılaştırması için kullan.

SQL sorgusunu yaz:"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": AGENT_CONFIGS["sql_agent"]["system_prompt"]},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,   # Deterministik çıktı için sıcaklık sıfır
    )

    sql_query = response.choices[0].message.content.strip()
    # Modelin eklediği ```sql ... ``` bloklarını temizle
    sql_query = sql_query.replace("```sql", "").replace("```", "").strip()

    state["sql_query"] = sql_query
    state["iteration"] = iteration + 1

    return state


def execute_sql(state: AgentState) -> AgentState:
    """
    Üretilen SQL sorgusunu veritabanında çalıştırır.
    Noktalı virgülle ayrılmış birden fazla ifadeyi sırayla işler.
    Başarıda query_result doldurulur; hata durumunda error ajanı devreye girer.
    """
    sql_query = state["sql_query"]

    try:
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Birden fazla SQL ifadesini ayır; boş olanları at
        statements  = [s.strip() for s in sql_query.split(";") if s.strip()]
        all_results = []

        for i, statement in enumerate(statements):
            cursor.execute(statement)
            rows = cursor.fetchall()

            if rows:
                col_names = [d[0] for d in cursor.description]
                # Her satırı kolon-değer sözlüğüne dönüştür; max 100 satırla sınırla
                formatted = [dict(zip(col_names, row)) for row in rows[:100]]

                if len(statements) > 1:
                    # Çoklu sorgu varsa hangi ifadeye ait olduğunu etiketle
                    all_results.append({
                        f"query_{i + 1}":     formatted,
                        f"query_{i + 1}_sql": statement,
                    })
                else:
                    all_results = formatted

        conn.close()

        state["query_result"] = (
            json.dumps(all_results, indent=2, ensure_ascii=False)
            if all_results
            else "Sonuç bulunamadı."
        )
        state["error"] = ""

    except Exception as e:
        # Hatayı state'e yaz; error_agent düzeltmeyi denesin
        state["error"]        = f"SQL Hatası: {str(e)}"
        state["query_result"] = ""

    return state


def _has_fanout_risk(sql: str) -> bool:
    """
    SQL sorgusunda fan-out (kartezyen çarpım) riski olup olmadığını
    tamamen Python ile tespit eder — LLM çağrısı yapmaz.

    Fan-out riski için üç koşulun birlikte gerçekleşmesi gerekir:
    1. En az bir JOIN var        (birden fazla tablo birleştiriliyor)
    2. En az bir agregasyon var  (SUM / COUNT / AVG / MAX / MIN)
    3. CTE (WITH bloğu) yok     (CTE varsa geliştirici zaten ayırmış demektir)

    Bu koşullar sağlanmadığında LLM'e hiç gidilmez; sorgu olduğu gibi geçer.
    """
    sql_upper = sql.upper().strip()

    has_join = " JOIN " in sql_upper
    has_agg  = any(f in sql_upper for f in ["SUM(", "COUNT(", "AVG(", "MAX(", "MIN("])
    has_cte  = sql_upper.startswith("WITH ")   # CTE varsa genellikle doğru yazılmıştır

    return has_join and has_agg and not has_cte


def sql_validator_agent(state: AgentState) -> AgentState:
    """
    SQL Kalite Denetçisi — sql_agent ile execute_sql arasında çalışır.

    Amacı: Fan-out (kartezyen çarpım) hatasını çalıştırmadan önce yakalamak.
    Fan-out, birden fazla tablodan SUM/COUNT/AVG yapılırken tablolar doğrudan
    JOIN edildiğinde oluşur; satırlar çarpışır ve sonuçlar yanlış şişer.

    Çalışma mantığı:
    1. _has_fanout_risk() ile tamamen deterministik Python tespiti yap.
       → Risk yoksa LLM çağrısı YAPMA, SQL'i olduğu gibi geçir.
    2. Risk tespit edilirse LLM'e gönder: "Bu SQL'i CTE yapısına çevir."
       → LLM burada yalnızca düzeltme işi yapıyor, karar vermiyor.
    3. Düzeltilmiş SQL'i state'e yaz.
    """
    sql_query = state["sql_query"]
    question  = state["question"]

    # ── Deterministik ön-kontrol (LLM yok) ───────────────────────────────────
    if not _has_fanout_risk(sql_query):
        return state   # Güvenli; LLM çağrısı yapmadan doğrudan geç

    # ── LLM ile düzeltme ──────────────────────────────────────────────────────
    # Buraya sadece _has_fanout_risk() True döndürdüğünde geliniyor.
    # LLM'in görevi "karar vermek" değil, yalnızca "CTE'ye dönüştürmek".
    prompt = f"""Aşağıdaki SQL sorgusunda fan-out (kartezyen çarpım) riski tespit edildi.
Sorguyu, her agregasyonun ayrı bir CTE içinde yapıldığı güvenli yapıya dönüştür.

SORUN:
JOIN edilen birden fazla tablodan aynı sorguda SUM/COUNT/AVG yapılıyor.
Bu durum satırların çarpışmasına ve yanlış sonuçlara yol açar.

ÇÖZÜM — Her agregasyonu ayrı CTE'ye al:
    WITH tx AS (
        SELECT category_id, SUM(amount) AS total
        FROM transactions GROUP BY category_id
    ),
    inv AS (
        SELECT category_id, COUNT(*) AS cnt
        FROM invoices WHERE status = 'overdue' GROUP BY category_id
    )
    SELECT c.category_name, tx.total, COALESCE(inv.cnt, 0)
    FROM categories c
    JOIN tx       ON tx.category_id  = c.category_id
    LEFT JOIN inv ON inv.category_id = c.category_id

Orijinal Soru: {question}

Düzeltilecek SQL:
{sql_query}

CTE yapısına dönüştürülmüş SQL'i yaz. Yalnızca SQL döndür; açıklama veya markdown ekleme."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": AGENT_CONFIGS["sql_validator_agent"]["system_prompt"]},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
        timeout=30,
    )

    validated_sql = response.choices[0].message.content.strip()
    validated_sql = validated_sql.replace("```sql", "").replace("```", "").strip()

    state["sql_query"] = validated_sql
    return state


def error_agent(state: AgentState) -> AgentState:
    """
    Başarısız SQL sorgusunu ve hata mesajını analiz ederek düzeltilmiş
    bir sorgu üretir. 3 denemeden sonra vazgeçer.
    """
    error     = state["error"]
    sql_query = state["sql_query"]
    question  = state["question"]
    iteration = state.get("iteration", 0)

    # Maksimum deneme sayısına ulaşıldıysa kullanıcıya açıklayıcı mesaj ver
    if iteration > 3:
        state["final_answer"] = (
            f"Üzgünüm, bu soru için doğru SQL sorgusunu üretemiyorum. "
            f"Hata: {error}\n\nSoruyu farklı bir ifadeyle sormayı deneyebilirsiniz."
        )
        return state

    prompt = f"""Aşağıdaki SQL sorgusu hata verdi. Lütfen düzelt.

{SCHEMA_INFO}

Orijinal Soru: {question}

Hatalı SQL:
{sql_query}

Hata Mesajı:
{error}

Önemli: Birden fazla tablodan SUM/COUNT/AVG hesaplanıyorsa her agregasyonu
ayrı bir CTE içinde yap, sonunda birleştir. Aksi hâlde JOIN fan-out nedeniyle
sonuçlar hatalı şişer.

Düzeltilmiş SQL sorgusunu yaz (yalnızca SQL, açıklama yok):"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": AGENT_CONFIGS["error_agent"]["system_prompt"]},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
    )

    corrected = response.choices[0].message.content.strip()
    corrected = corrected.replace("```sql", "").replace("```", "").strip()

    state["sql_query"]  = corrected
    state["error"]      = ""          # Hatayı temizle; execute_sql yeniden denesin
    state["iteration"]  = iteration + 1

    return state


def analysis_agent(state: AgentState) -> AgentState:
    """
    Ham sorgu sonuçlarını kullanıcıya yönelik anlaşılır bir metne dönüştürür.
    Sayıları, listeleri ve finansal yorumları içerir.
    """
    question     = state["question"]
    sql_query    = state["sql_query"]
    query_result = state["query_result"]

    prompt = f"""Aşağıdaki veritabanı sorgu sonuçlarını kullanıcıya sade bir dille anlat.

Kullanıcı Sorusu: {question}

Kullanılan SQL:
{sql_query}

Sorgu Sonuçları:
{query_result}

Yanıt oluştururken:
- Soruyu doğrudan yanıtla.
- Parasal değerleri okunabilir biçimde yaz (ör. 12.450,00 TRY).
- Birden fazla sonuç varsa madde listeleri kullan.
- Çok parçalı sorularda her bölümü ayrı ele al.
- Kısa ve odaklı kal; gereksiz tekrar yapma.

Yanıt:"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": AGENT_CONFIGS["analysis_agent"]["system_prompt"]},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.7,   # Orta sıcaklık: doğal ve akıcı bir anlatım için
    )

    state["final_answer"] = response.choices[0].message.content.strip()
    return state


def decide_graph_need(state: AgentState) -> AgentState:
    """
    Sorgu sonuçlarının görsel temsile uygun olup olmadığına karar verir.
    Tek değer veya kısa metin listeleri için grafik üretilmez.
    """
    question     = state["question"]
    query_result = state["query_result"]

    # Sonuç yoksa veya önceki adımda hata varsa grafik gereksiz
    if not query_result or query_result == "Sonuç bulunamadı." or state.get("error"):
        state["needs_graph"] = False
        state["graph_type"]  = ""
        return state

    prompt = f"""Soru ve sorgu sonucuna bakarak grafik gerekip gerekmediğine karar ver.

Soru: {question}

Sorgu Sonucu (ilk 500 karakter):
{query_result[:500]}

Karar kriterleri:
- Zaman içindeki trend       → line (çizgi)
- Kategoriler arası karşılaştırma → bar (çubuk)
- Oran / yüzde dağılımı      → pie (pasta)
- Korelasyon / nokta dağılımı → scatter (nokta)
- Tek sayı veya kısa metin   → grafik gerekmiyor

JSON formatında yanıt ver:
{{"needs_graph": true/false, "graph_type": "bar/line/pie/scatter/none", "reason": "kısa açıklama"}}"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Veri görselleştirme uzmanısın. Hangi sorguların grafikten fayda sağlayacağını belirle."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    decision             = json.loads(response.choices[0].message.content)
    state["needs_graph"] = decision.get("needs_graph", False)
    state["graph_type"]  = decision.get("graph_type", "none")

    return state


def viz_agent(state: AgentState) -> AgentState:
    """
    LLM aracılığıyla Plotly kodu üretir ve exec() ile çalıştırarak
    interaktif bir grafik oluşturur. Chainlit'e JSON olarak iletilir.

    Not: exec() güvenlik riski taşır; üretim ortamında sandboxlama önerilir.
    """
    query_result = state["query_result"]
    graph_type   = state["graph_type"]
    question     = state["question"]
    plotly_code  = ""   # Hata mesajında referans için önceden tanımla

    try:
        results = json.loads(query_result)
        if not results:
            state["graph_json"] = ""
            return state

        df      = pd.DataFrame(results)
        columns = df.columns.tolist()
        sample  = df.head(5).to_dict("records")

        prompt = f"""Aşağıdaki finansal veriyi görselleştiren Plotly kodu yaz.

Kullanıcı Sorusu: {question}
Grafik Türü: {graph_type}
Kolonlar: {columns}
Örnek Veri (ilk 5 satır): {json.dumps(sample, indent=2, ensure_ascii=False)}
Toplam Satır: {len(df)}

Gereksinimler:
1. plotly.graph_objects veya plotly.express kullan.
2. Veri 'df' adlı bir pandas DataFrame olarak hazır mevcut.
3. {graph_type} türünde grafik oluştur.
4. 20'den fazla satır varsa ilk 20'yi kullan.
5. Türkçe başlık ve eksen etiketleri ekle.
6. Figür değişkeni kesinlikle 'fig' olarak adlandırılmalı.
7. import ifadesi, fig.show() çağrısı veya markdown ekleme — sadece Python kodu.
8. Renk, hover bilgisi ve responsive boyutlandırma ile görsel kaliteyi artır.

Plotly kodunu yaz:"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": AGENT_CONFIGS["viz_agent"]["system_prompt"]},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.3,
            timeout=30,   # 30 saniye içinde yanıt gelmezse TimeoutError fırlatır
        )

        plotly_code = response.choices[0].message.content.strip()
        plotly_code = plotly_code.replace("```python", "").replace("```", "").strip()

        # Plotly kütüphanelerini exec ortamına aktar
        import plotly.graph_objects as go
        import plotly.express as px

        exec_env = {"df": df, "pd": pd, "json": json, "go": go, "px": px}
        exec(plotly_code, exec_env)   # LLM tarafından üretilen kodu çalıştır

        fig = exec_env.get("fig")
        if fig is None:
            raise ValueError("Üretilen kod 'fig' değişkenini oluşturmadı.")

        # Chainlit'in cl.Plotly elementi için JSON formatına çevir
        state["graph_json"] = fig.to_json()

    except Exception as e:
        print(f"Grafik üretim hatası: {e}")
        print(f"Üretilen kod:\n{plotly_code}")
        state["graph_json"] = ""

    return state


def sanity_check_agent(state: AgentState) -> AgentState:
    """
    Sonuç Akıl Yürütücüsü — execute_sql başarılı olduktan sonra çalışır.

    SQL çalışmış ama sonuçlar yanlış olabilir: fan-out'tan kalmış şişmiş sayılar,
    yanlış tablo JOIN'i veya mantıksız değerler gibi.

    Çalışma mantığı:
    1. Soruyu, SQL'i ve sorgu sonuçlarını birlikte değerlendirir.
    2. "Bu sonuçlar bu soru için mantıklı mı?" sorusunu sorar.
    3. Şüpheli bulursa: sanity_passed=False, sorun açıklamasını sanity_issue'ya yazar.
       → sql_agent bu feedback'i alarak SQL'i yeniden üretir.
    4. sanity_retried=True ise (zaten bir kez yeniden denendi) döngüyü kırıp geçer.

    Döngü koruması: sanity_retried bayrağı True olduğunda ikinci kez yeniden
    üretmeye zorlamaz; sonsuz döngü oluşmaz.
    """
    question     = state["question"]
    sql_query    = state["sql_query"]
    query_result = state["query_result"]

    # Sonuç yoksa kontrol etmeye gerek yok
    if not query_result or query_result == "Sonuç bulunamadı.":
        state["sanity_passed"] = True
        state["sanity_issue"]  = ""
        return state

    # Döngü koruması: zaten bir kez yeniden denendiyse geç
    if state.get("sanity_retried", False):
        state["sanity_passed"] = True
        state["sanity_issue"]  = ""
        return state

    # Sonucu kırp — LLM context limitini korumak için
    result_preview = query_result[:1000]

    prompt = f"""Bir SQL sorgusunun sonuçlarının mantıklı olup olmadığını değerlendir.

Veritabanı bağlamı:
- Kişisel finans veritabanı (2024–2025 sentetik veri)
- Toplam ~510 işlem, 200 fatura, 5 hesap, 15 kategori
- Aylık maaş: ~18.000–22.000 TRY, aylık kira: ~8.000–9.000 TRY
- Tek bir kategorinin yıllık toplam harcaması genellikle 200.000 TRY'yi geçmez

Kullanıcı Sorusu: {question}

Çalıştırılan SQL:
{sql_query}

Sorgu Sonuçları (ilk 1000 karakter):
{result_preview}

Kontrol et:
1. Parasal değerler makul aralıkta mı? (milyonlarca TRY olmamalı)
2. Sayımlar (COUNT) gerçekçi mi? (200 fatura varken 13.000 overdue olamaz)
3. Sonuç yapısı soruyla örtüşüyor mu?
4. Aynı kategori birden fazla kez mi listelenmiş? (GROUP BY hatası)

JSON formatında yanıt ver:
{{
    "is_reasonable": true/false,
    "issue": "sorun varsa kısa açıklama, yoksa boş string"
}}"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": AGENT_CONFIGS["sanity_check_agent"]["system_prompt"]},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        timeout=30,
    )

    result = json.loads(response.choices[0].message.content)

    if result.get("is_reasonable", True):
        state["sanity_passed"] = True
        state["sanity_issue"]  = ""
    else:
        # Sorun bulundu: SQL yeniden üretilecek; döngü korumasını etkinleştir
        state["sanity_passed"]  = False
        state["sanity_issue"]   = result.get("issue", "Sonuçlar mantıklı görünmüyor.")
        state["sanity_retried"] = True   # Bir sonraki turda döngüye girmemek için

    return state


# ══════════════════════════════════════════════════════════════════════════════
# YÖNLENDİRME FONKSİYONLARI
# LangGraph'teki koşullu kenarlar bu fonksiyonlar aracılığıyla çözümlenir.
# ══════════════════════════════════════════════════════════════════════════════

def check_scope(state: AgentState) -> str:
    """Guardrails sonucuna göre SQL üretimine devam et veya iş akışını sonlandır."""
    return "in_scope" if state.get("is_in_scope", True) else "out_of_scope"


def should_retry(state: AgentState) -> str:
    """
    SQL yürütme sonucuna göre sonraki adımı belirler.
    - Hata ve deneme limiti aşılmadı → error_agent'e yönlendir (retry)
    - Hata ve limit aşıldı           → analysis_agent'e geç (end)
    - Hata yok                       → analysis_agent'e geç (success)
    """
    if state.get("error"):
        return "retry" if state.get("iteration", 0) <= 3 else "end"
    return "success"


def should_regenerate_sql(state: AgentState) -> str:
    """
    Sanity check sonucuna göre yönlendir.
    - sanity_passed=False → sql_agent'e geri dön (feedback ile yeniden üret)
    - sanity_passed=True  → analysis_agent'e ilerle
    """
    return "regenerate" if not state.get("sanity_passed", True) else "proceed"


def should_generate_graph(state: AgentState) -> str:
    """Grafik kararına göre viz_agent'e veya doğrudan END'e yönlendir."""
    return "viz_agent" if state.get("needs_graph", False) else "skip_graph"


# ══════════════════════════════════════════════════════════════════════════════
# LANGGRAPH İŞ AKIŞI
# ══════════════════════════════════════════════════════════════════════════════

def create_finance_graph():
    """
    LangGraph state machine'ini düğümler, kenarlar ve koşullu yönlendirmelerle
    oluşturur, derler ve döner.
    """
    workflow = StateGraph(AgentState)

    # ── Düğümleri kaydet ──────────────────────────────────────────────────────
    workflow.add_node("guardrails_agent",    guardrails_agent)
    workflow.add_node("sql_agent",           sql_agent)
    workflow.add_node("sql_validator_agent", sql_validator_agent)
    workflow.add_node("execute_sql",         execute_sql)
    workflow.add_node("sanity_check_agent",  sanity_check_agent)   # ← yeni
    workflow.add_node("analysis_agent",      analysis_agent)
    workflow.add_node("error_agent",         error_agent)
    workflow.add_node("decide_graph_need",   decide_graph_need)
    workflow.add_node("viz_agent",           viz_agent)

    # ── Başlangıç noktası: kapsam kontrolü ───────────────────────────────────
    workflow.set_entry_point("guardrails_agent")

    # Kapsam içindeyse SQL üretimine geç; değilse bitir
    workflow.add_conditional_edges(
        "guardrails_agent",
        check_scope,
        {"in_scope": "sql_agent", "out_of_scope": END},
    )

    # SQL üretiminden önce doğrulama ajanı geçiyor; fan-out varsa burada düzeltilir
    workflow.add_edge("sql_agent",           "sql_validator_agent")
    workflow.add_edge("sql_validator_agent", "execute_sql")

    # Yürütme sonrası: başarı → sanity check, SQL hatası → düzeltme, limit aşımı → analiz
    workflow.add_conditional_edges(
        "execute_sql",
        should_retry,
        {"success": "sanity_check_agent", "retry": "error_agent", "end": "analysis_agent"},
    )

    # Sanity check: sonuçlar mantıklıysa analiz, değilse SQL'i yeniden üret
    workflow.add_conditional_edges(
        "sanity_check_agent",
        should_regenerate_sql,
        {"regenerate": "sql_agent", "proceed": "analysis_agent"},
    )

    # Hata düzeltme döngüsü: düzeltilen sorguyu tekrar çalıştır
    workflow.add_edge("error_agent",    "execute_sql")
    workflow.add_edge("analysis_agent", "decide_graph_need")

    # Grafik kararı: üretilecekse viz_agent'e, yoksa bitir
    workflow.add_conditional_edges(
        "decide_graph_need",
        should_generate_graph,
        {"viz_agent": "viz_agent", "skip_graph": END},
    )

    workflow.add_edge("viz_agent", END)

    return workflow.compile()


# Uygulama başlarken bir kez derlenir; her istekte yeniden oluşturulmaz
finance_graph = create_finance_graph()


# ══════════════════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ══════════════════════════════════════════════════════════════════════════════

def generate_graph_visualization(output_path: str = "finance_workflow.png") -> str:
    """
    LangGraph iş akışını PNG dosyasına çizer.
    Geliştirme aşamasında mimariyi görselleştirmek için kullanılır.
    pygraphviz veya grandalf paketlerinden biri gereklidir.

    Args:
        output_path: PNG dosyasının kaydedileceği yol

    Returns:
        Oluşturulan dosyanın yolu; hata durumunda None
    """
    try:
        image_bytes = finance_graph.get_graph().draw_mermaid_png()
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        print(f"İş akışı diyagramı kaydedildi: {output_path}")
        return output_path
    except Exception as e:
        print(f"Diyagram üretme hatası: {e}")
        return None


async def process_question_stream(question: str):
    """
    Doğal dildeki soruyu işler ve ajan adımlarını gerçek zamanlı akışa alır.
    Chainlit arayüzüne olay nesneleri (dict) gönderir.

    Olay tipleri:
        node_start  → Bir düğüm çalışmaya başladı
        node_end    → Bir düğüm tamamlandı (output içerir)
        final       → Tüm akış bitti (nihai result içerir)
        error       → Beklenmeyen bir hata oluştu

    Args:
        question: Kullanıcının doğal dildeki sorusu

    Yields:
        dict: {"type": ..., ...} formatında olay nesneleri
    """
    initial_state = AgentState(
        question=question,
        sql_query="",
        query_result="",
        final_answer="",
        error="",
        iteration=0,
        needs_graph=False,
        graph_type="",
        graph_json="",
        is_in_scope=True,
        sanity_passed=True,    # varsayılan: geç (sorun yoksa işlem yapma)
        sanity_issue="",
        sanity_retried=False,
    )

    current_state = initial_state.copy()

    # Akış olaylarında izlenecek düğüm adları
    TRACKED_NODES = {
        "guardrails_agent", "sql_agent",    "sql_validator_agent", "execute_sql",
        "sanity_check_agent", "analysis_agent", "error_agent",
        "decide_graph_need", "viz_agent",
    }

    try:
        async for event in finance_graph.astream_events(
            initial_state,
            config={"recursion_limit": 50},
            version="v1",
        ):
            event_type = event.get("event")
            node_name  = event.get("name", "")

            if event_type == "on_chain_start" and node_name in TRACKED_NODES:
                yield {"type": "node_start", "node": node_name, "input": current_state}

            elif event_type == "on_chain_end" and node_name in TRACKED_NODES:
                output = event.get("data", {}).get("output", {})
                # output boş dict olsa bile node_end'i tetikle;
                # aksi hâlde Chainlit adımı hiç kapanmaz
                if output is not None:
                    current_state.update(output)
                yield {
                    "type":   "node_end",
                    "node":   node_name,
                    "output": output or {},
                    "state":  current_state.copy(),
                }

        yield {"type": "final", "result": current_state}

    except Exception as e:
        yield {"type": "error", "error": str(e)}


if __name__ == "__main__":
    print("Finans Asistanı — 'chainlit run app.py' komutuyla başlatın.")
