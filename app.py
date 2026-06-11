"""
Finans Asistanı — Chainlit Web Arayüzü
=======================================
Kullanıcı mesajlarını alır, LangGraph ajan akışını gerçek zamanlı
olarak adım adım görüntüler ve nihai yanıtı (metin + grafik) gönderir.

Başlatma:
    chainlit run app.py

Bağımlılıklar:
    text2sql_agent.process_question_stream  → ajan akışı
    text2sql_agent.generate_graph_visualization → iş akışı diyagramı (opsiyonel)
"""

import json
import chainlit as cl
from text2sql_agent import process_question_stream, generate_graph_visualization

# ── İş akışı diyagramını üret (opsiyonel) ─────────────────────────────────────
# Uygulama başlarken LangGraph mimarisinin PNG görselini oluşturmaya çalışır.
# pygraphviz veya grandalf kurulu değilse sessizce atlanır; uygulama etkilenmez.
try:
    diagram_path = generate_graph_visualization("finance_workflow.png")
    if diagram_path:
        print(f"✅ İş akışı diyagramı oluşturuldu: {diagram_path}")
except Exception as e:
    print(f"⚠️  Diyagram oluşturulamadı: {e}")


# ── Düğüm Görüntü Adları ───────────────────────────────────────────────────────
# LangGraph düğüm adlarını (kod içi) Chainlit adım panelinde gösterilecek
# kullanıcı dostu etiketlere eşler.
NODE_DISPLAY_NAMES = {
    "guardrails_agent":    "🛡️  Kapsam Kontrolü",
    "sql_agent":           "📝 SQL Üretimi",
    "sql_validator_agent": "🔍 SQL Doğrulama",
    "execute_sql":         "⚙️  Sorgu Çalıştırma",
    "sanity_check_agent":  "🧠 Sonuç Akıl Yürütme",   # ← yeni
    "analysis_agent":      "💬 Yanıt Üretimi",
    "error_agent":         "🔧 Hata Düzeltme",
    "decide_graph_need":   "📊 Grafik Kararı",
    "viz_agent":           "📈 Grafik Üretimi",
}


# ══════════════════════════════════════════════════════════════════════════════
# CHAINLIT OLAY YAKALAYICILARI
# ══════════════════════════════════════════════════════════════════════════════

@cl.on_chat_start
async def start():
    """
    Yeni bir sohbet oturumu başladığında tetiklenir.
    Kullanıcıya hoş geldiniz mesajı ve örnek sorular gönderilir.
    """
    await cl.Message(
        content=(
            "👋 **Finans Asistanına Hoş Geldiniz!**\n\n"
            "2024–2025 dönemine ait finansal verilerinizi doğal dille sorgulayabilirsiniz.\n\n"
            "**Örnek sorular:**\n"
            "- Bu yılki toplam gelir ve giderim ne kadar?\n"
            "- En çok harcama yaptığım 5 kategori neler?\n"
            "- Hangi aylarda bütçemi aştım?\n"
            "- Ödenmemiş veya gecikmiş faturalarım var mı?\n"
            "- Kredi kartı ile yapılan harcamaların toplamı nedir?\n"
            "- Aylık ortalama market harcamam ne kadar?\n"
            "- Maaş dışı gelirlerim neler?\n\n"
            "Sorunuzu yazın, gerisini ben halledeyim! 🚀"
        )
    ).send()


@cl.on_message
async def main(message: cl.Message):
    """
    Kullanıcıdan gelen her mesajda tetiklenir.
    Ajan akışını başlatır, her adımı canlı olarak Chainlit paneline yansıtır
    ve akış tamamlandığında nihai yanıtı (metin + grafik) gönderir.
    """
    user_question = message.content
    final_result  = None
    node_steps    = {}   # Açık Chainlit alt adımlarını takip etmek için

    # Tüm ajan adımlarını tek bir üst blok altında grupla
    async with cl.Step(name="🤖 Ajan İş Akışı", type="llm") as workflow_step:
        try:
            async for event in process_question_stream(user_question):
                event_type = event.get("type")

                # ── Düğüm çalışmaya başladı ────────────────────────────────────
                if event_type == "node_start":
                    node_name    = event["node"]
                    display_name = NODE_DISPLAY_NAMES.get(node_name, node_name)

                    # Bu düğüm için iç içe bir Chainlit adımı oluştur
                    node_step = cl.Step(
                        name=display_name,
                        type="tool",
                        parent_id=workflow_step.id,
                    )
                    await node_step.send()
                    node_steps[node_name] = node_step

                # ── Düğüm tamamlandı ───────────────────────────────────────────
                elif event_type == "node_end":
                    node_name = event["node"]
                    output    = event["output"]

                    if node_name not in node_steps:
                        continue

                    # Düğüm çıktısını biçimlendir ve adımı güncelle
                    node_steps[node_name].output = _format_node_output(node_name, output)
                    await node_steps[node_name].update()

                # ── Tüm akış tamamlandı ────────────────────────────────────────
                elif event_type == "final":
                    final_result = event["result"]

                # ── Beklenmeyen hata ───────────────────────────────────────────
                elif event_type == "error":
                    workflow_step.output = f"❌ **Hata:** {event['error']}"
                    await workflow_step.update()
                    return

            workflow_step.output = "✅ İş akışı tamamlandı."
            await workflow_step.update()

        except Exception as e:
            workflow_step.output = f"❌ **Beklenmeyen hata:** {str(e)}"
            await workflow_step.update()
            raise

    # ── Nihai yanıtı gönder ────────────────────────────────────────────────────
    if not final_result:
        return

    # SQL sorgusu oluşturulduysa göster; selamlama / kapsam dışı sorularda oluşmaz
    if final_result.get("sql_query") and final_result["sql_query"].strip():
        response_text = (
            f"**Üretilen SQL:**\n"
            f"```sql\n{final_result['sql_query']}\n```\n\n"
            f"**Yanıt:**\n{final_result['final_answer']}"
        )
    else:
        # Selamlama veya kapsam dışı soru — sadece metin yanıt
        response_text = final_result["final_answer"]

    # 3 deneme sonunda hâlâ hata varsa kullanıcıyı bilgilendir
    if final_result.get("error"):
        response_text += f"\n\n⚠️ **Not:** {final_result['error']}"

    await cl.Message(content=response_text).send()

    # ── Grafik gönder (varsa) ──────────────────────────────────────────────────
    if final_result.get("needs_graph") and final_result.get("graph_json"):
        import plotly.graph_objects as go

        # JSON'dan Plotly figürünü geri yükle ve Chainlit elementi olarak gönder
        fig        = go.Figure(json.loads(final_result["graph_json"]))
        chart_type = final_result.get("graph_type", "grafik").title()

        graph_element = cl.Plotly(
            name=f"{final_result.get('graph_type', 'chart')}_visualization",
            figure=fig,
            display="inline",
        )

        await cl.Message(
            content=(
                f"📊 **İnteraktif {chart_type} Grafiği**\n\n"
                "*Üzerine gelin, yakınlaştırın veya sürükleyin!*"
            ),
            elements=[graph_element],
        ).send()


@cl.on_chat_end
async def end():
    """Sohbet oturumu kapandığında veda mesajı gönderir."""
    await cl.Message(
        content="Görüşmek üzere! Finans Asistanını kullandığınız için teşekkürler. 👋"
    ).send()


# ══════════════════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ══════════════════════════════════════════════════════════════════════════════

def _format_node_output(node_name: str, output: dict) -> str:
    """
    Her düğümün çıktısını Chainlit adım panelinde gösterilmek üzere biçimlendirir.
    Düğüme özgü alakalı alanlar seçilir; uzun içerikler kırpılır.

    Args:
        node_name: LangGraph düğümünün kod içi adı
        output:    Düğümün state güncellemesi (dict)

    Returns:
        Markdown biçiminde biçimlendirilmiş çıktı metni
    """
    if node_name == "guardrails_agent":
        is_in_scope = output.get("is_in_scope", True)
        if is_in_scope:
            return "✅ Soru finans verileri kapsamında, devam ediliyor."
        return "⛔ Soru kapsam dışında; SQL üretimi atlanıyor."

    elif node_name == "sanity_check_agent":
        passed = output.get("sanity_passed", True)
        issue  = output.get("sanity_issue", "")
        if passed:
            return "✅ Sonuçlar mantıklı, devam ediliyor."
        return f"⚠️ **Şüpheli sonuç tespit edildi, SQL yeniden üretiliyor:**\n{issue}"

    elif node_name == "sql_validator_agent":
        # Doğrulama öncesi ve sonrası SQL'i karşılaştırarak değişiklik olup olmadığını göster
        validated_sql = output.get("sql_query", "")
        return (
            f"**Doğrulanmış SQL:**\n```sql\n{validated_sql}\n```"
            if validated_sql
            else "ℹ️ Fan-out riski yok, SQL değiştirilmedi."
        )

    elif node_name == "sql_agent":
        sql = output.get("sql_query", "")
        return f"**Üretilen SQL:**\n```sql\n{sql}\n```"

    elif node_name == "execute_sql":
        if output.get("error"):
            return f"❌ **Hata:**\n```\n{output['error']}\n```"
        result = output.get("query_result", "")
        # Arayüzde yer kaplamasın diye uzun sonuçları kırp
        if len(result) > 500:
            result = result[:500] + "\n... (kısaltıldı)"
        return f"**Sorgu Sonuçları:**\n```json\n{result}\n```"

    elif node_name == "error_agent":
        corrected = output.get("sql_query", "")
        iteration = output.get("iteration", 0)
        return f"**Düzeltilmiş SQL (Deneme {iteration}):**\n```sql\n{corrected}\n```"

    elif node_name == "analysis_agent":
        answer = output.get("final_answer", "")
        return f"**Yanıt:**\n{answer}"

    elif node_name == "decide_graph_need":
        needs_graph = output.get("needs_graph", False)
        graph_type  = output.get("graph_type", "")
        if needs_graph:
            return f"✅ **Grafik Gerekiyor:** {graph_type.upper()} türü seçildi."
        return "ℹ️ **Bu sorgu için grafik gerekmiyor.**"

    elif node_name == "viz_agent":
        return (
            "✅ Grafik başarıyla üretildi."
            if output.get("graph_json")
            else "⚠️ Grafik üretilemedi, metin yanıt yeterli."
        )

    # Bilinmeyen düğüm — ham çıktıyı göster
    return str(output)


if __name__ == "__main__":
    # Bu dosya doğrudan çalıştırılmaz; Chainlit CLI'si ile başlatılır:
    #   chainlit run app.py
    pass
