"""
tests/test_qna_ai_engine.py
-----------------------------
qna_ai_engine.py'nin saf/deterministik kısımlarını kapsar:
  - build_grounded_prompt: fact listesinden Ollama'ya gidecek prompt'u kurar
  - parse_model_response: model çıktısını (NEEDS_CLARIFICATION formatı +
    olası "Thinking..." bloğu) draft/clarification'a ayrıştırır
  - generate_draft_answer: Ollama HTTP çağrısını (requests.post) mock'layıp
    uçtan uca akışı doğrular — GERÇEK Ollama'ya asla istek atmaz.

29.08.2026 Faz 0 sohbetinde M3/16GB üzerinde gemma4:e4b ile ampirik olarak
doğrulanan davranış: fact'lerle grounded prompt verildiğinde model ya
sadece verilen bilgiyi kullanıyor ya da NEEDS_CLARIFICATION: ile başlayan
bir çıktı üretiyor. Bu testler o davranışın PARSE edilmesini kapsıyor,
modelin kendisini değil (o ampirik olarak zaten doğrulandı).
"""

from unittest.mock import patch

import pytest

from qna_ai_engine import build_grounded_prompt, generate_draft_answer, parse_model_response


def test_build_grounded_prompt_includes_all_facts():
    facts = [
        {"topic": "pil", "fact_text": "4 adet saat pili kullanılır, şarjlı değildir."},
        {"topic": "garanti", "fact_text": "2 yıl garantilidir."},
    ]
    prompt = build_grounded_prompt(
        sku="SH-8IN1-METER",
        question_text="Pil ömrü ne kadar?",
        facts=facts,
    )
    assert "4 adet saat pili" in prompt
    assert "2 yıl garantilidir" in prompt
    assert "Pil ömrü ne kadar?" in prompt
    assert "NEEDS_CLARIFICATION" in prompt  # talimat formatı prompt'ta açıkça geçmeli


def test_build_grounded_prompt_with_no_facts_still_instructs_clarification():
    """Hiç fact yoksa bile prompt, modelin uydurmak yerine NEEDS_CLARIFICATION
    demesini talimat olarak içermeli — boş bilgi tabanı = otomatik clarification
    değil, modelin kendisi karar veriyor (ama talimat her zaman prompt'ta var)."""
    prompt = build_grounded_prompt(sku="SH-8IN1-METER", question_text="Garanti süresi nedir?", facts=[])
    assert "NEEDS_CLARIFICATION" in prompt
    assert "ÜRÜN BİLGİ TABANI" in prompt


def test_parse_model_response_plain_answer():
    raw = "Cihazımız pil ile çalışır, şarj edilebilir değildir."
    result = parse_model_response(raw)
    assert result["needs_clarification"] is False
    assert result["draft_text"] == "Cihazımız pil ile çalışır, şarj edilebilir değildir."
    assert result["clarification_prompt"] is None


def test_parse_model_response_needs_clarification():
    raw = "NEEDS_CLARIFICATION: Garanti süresi bilgi tabanında yer almıyor."
    result = parse_model_response(raw)
    assert result["needs_clarification"] is True
    assert result["clarification_prompt"] == "Garanti süresi bilgi tabanında yer almıyor."
    assert result["draft_text"] is None


def test_parse_model_response_strips_thinking_block():
    """Gemma4/Qwen3.5 gibi 'thinking' modlu modeller '...done thinking.'
    öncesinde uzun bir akıl yürütme bloğu üretiyor (29.08.2026 testinde
    gözlemlendi) — bu blok MÜŞTERİYE gönderilecek taslağa asla karışmamalı."""
    raw = (
        "Thinking...\n"
        "Thinking Process:\n"
        "1. Analyze the question...\n"
        "2. Draft the answer...\n"
        "...done thinking.\n\n"
        "Cihazımız pil ile çalışır, şarj edilebilir değildir."
    )
    result = parse_model_response(raw)
    assert result["needs_clarification"] is False
    assert "Thinking" not in result["draft_text"]
    assert result["draft_text"].strip() == "Cihazımız pil ile çalışır, şarj edilebilir değildir."


def test_parse_model_response_strips_thinking_block_before_clarification():
    raw = (
        "Thinking...\n"
        "1. Check knowledge base...\n"
        "...done thinking.\n\n"
        "NEEDS_CLARIFICATION: Su geçirmezlik derecesi bilgi tabanında yok."
    )
    result = parse_model_response(raw)
    assert result["needs_clarification"] is True
    assert result["clarification_prompt"] == "Su geçirmezlik derecesi bilgi tabanında yok."


def test_parse_model_response_empty_raises():
    with pytest.raises(ValueError):
        parse_model_response("")


@patch("qna_ai_engine.requests.post")
def test_generate_draft_answer_calls_ollama_and_parses(mock_post):
    mock_post.return_value.json.return_value = {
        "response": "NEEDS_CLARIFICATION: Garanti süresi bilgi tabanında yok."
    }
    mock_post.return_value.raise_for_status = lambda: None

    result = generate_draft_answer(
        sku="SH-8IN1-METER",
        question_text="Garanti süresi ne kadar?",
        facts=[{"topic": "pil", "fact_text": "4 adet saat pili."}],
    )

    assert result["needs_clarification"] is True
    assert result["model_used"] == "gemma4:e4b"
    # Ollama'ya gerçekten localhost API'sine, doğru model adıyla istek atıldığını doğrula
    call_kwargs = mock_post.call_args
    assert "localhost:11434" in call_kwargs.args[0] or "localhost:11434" in str(call_kwargs)
    assert call_kwargs.kwargs["json"]["model"] == "gemma4:e4b"
    assert call_kwargs.kwargs["json"]["stream"] is False


@patch("qna_ai_engine.requests.post")
def test_generate_draft_answer_raises_on_connection_error(mock_post):
    """Ollama servisi ayakta değilse (yerel makinede kapalıysa) sessizce
    None dönmek yerine açıkça hata fırlatmalı — 'no silent data absence'
    prensibi: taslak üretilemedi bilgisi kaybolmamalı."""
    import requests
    mock_post.side_effect = requests.exceptions.ConnectionError("refused")

    with pytest.raises(requests.exceptions.ConnectionError):
        generate_draft_answer(sku="SH-8IN1-METER", question_text="Pil ömrü?", facts=[])
