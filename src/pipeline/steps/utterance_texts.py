"""The spoken-prompt lines for every roster language (H6, AI-481).

Pure data, kept apart from the narration code so the RUF001 ignore (the
Greek, Cyrillic and Devanagari lines are written in their own scripts on
purpose) covers this file alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.pipeline.models import Language

UtteranceName = Literal["shelf_greeting", "story_start", "end_prompt", "audio_retry", "offline"]

# Final Italian copy, verbatim from docs/product.md **Spoken Prompts** —
# the slice-1 set plus the slice-2 failure prompts (AI-367).
IT_UTTERANCES: Mapping[UtteranceName, str] = {
    "shelf_greeting": "Ciao! Quale storia ascoltiamo oggi?",
    "story_start": "Si parte!",
    "end_prompt": "Fine! Ancora, o un'altra storia?",
    "audio_retry": "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.",
    "offline": "Le nuvole hanno preso le storie. Riprova tra poco!",
}

# The spoken prompts for every language in the roster (H6, AI-481): a plain
# mapping keyed by Language. Each non-Italian set matches the Italian lines in
# meaning, tone and length — warm and simple for a young child at bedtime —
# rather than translating them word for word. A test fails CI if any Language
# lacks any UtteranceName.
UTTERANCE_TEXTS: Mapping[Language, Mapping[UtteranceName, str]] = {
    "it": IT_UTTERANCES,
    # Final English copy (product.md calls English the source), verbatim
    # from docs/product.md **Spoken Prompts**.
    "en": {
        "shelf_greeting": "Hello! Which story shall we hear today?",
        "story_start": "Here we go!",
        "end_prompt": "The end! Again, or another story?",
        "audio_retry": "Oh! The story is napping. Tap the bird to wake it.",
        "offline": "The clouds took our stories. Try again soon!",
    },
    # Final Spanish copy, verbatim from docs/product.md **Spoken Prompts**.
    "es": {
        "shelf_greeting": "¡Hola! ¿Qué cuento escuchamos hoy?",
        "story_start": "¡Allá vamos!",
        "end_prompt": "¡Fin! ¿Otra vez, u otro cuento?",
        "audio_retry": "¡Oh! El cuento está durmiendo. Toca el pajarito para despertarlo.",
        "offline": "Las nubes se llevaron los cuentos. ¡Inténtalo pronto!",
    },
    # Machine-drafted, pending native review.
    "el": {
        "shelf_greeting": "Γεια σου! Ποια ιστορία θα ακούσουμε σήμερα;",
        "story_start": "Ξεκινάμε!",
        "end_prompt": "Τέλος! Πάλι, ή μια άλλη ιστορία;",
        "audio_retry": "Ω! Η ιστορία παίρνει έναν υπνάκο. Άγγιξε το πουλάκι να την ξυπνήσεις.",
        "offline": "Τα σύννεφα πήραν τις ιστορίες. Δοκίμασε ξανά σε λίγο!",
    },
    # Machine-drafted, pending native review.
    "de": {
        "shelf_greeting": "Hallo! Welche Geschichte hören wir heute?",
        "story_start": "Los geht's!",
        "end_prompt": "Ende! Nochmal, oder eine andere Geschichte?",
        "audio_retry": "Oh! Die Geschichte macht ein Nickerchen. Tipp auf das Vögelchen, um sie zu wecken.",
        "offline": "Die Wolken haben die Geschichten mitgenommen. Versuch es gleich nochmal!",
    },
    # Machine-drafted, pending native review.
    "bg": {
        "shelf_greeting": "Здравей! Коя приказка ще слушаме днес?",
        "story_start": "Тръгваме!",
        "end_prompt": "Край! Отново, или друга приказка?",
        "audio_retry": "О! Приказката си подремва. Докосни птичето, за да я събудиш.",
        "offline": "Облаците взеха приказките. Опитай пак след малко!",
    },
    # Machine-drafted, pending native review.
    "ru": {
        "shelf_greeting": "Привет! Какую сказку послушаем сегодня?",
        "story_start": "Поехали!",
        "end_prompt": "Конец! Ещё раз или другую сказку?",
        "audio_retry": "Ой! Сказка задремала. Коснись птички, чтобы её разбудить.",
        "offline": "Облака унесли сказки. Попробуй ещё раз чуть позже!",
    },
    # Machine-drafted, pending native review.
    "mr": {
        "shelf_greeting": "नमस्कार! आज आपण कोणती गोष्ट ऐकूया?",
        "story_start": "चला, सुरू करूया!",
        "end_prompt": "गोष्ट संपली! पुन्हा ऐकूया, की दुसरी गोष्ट?",
        "audio_retry": "अरे! गोष्ट डुलकी घेतेय. तिला उठवायला छोट्या पक्ष्याला हात लाव.",
        "offline": "ढगांनी गोष्टी नेल्या. थोड्या वेळाने पुन्हा प्रयत्न कर!",
    },
}
