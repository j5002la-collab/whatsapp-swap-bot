"""Internationalization: language detection + translation loader.
Auto-detects language from WhatsApp phone country code, with manual override.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("i18n")

TRANSLATIONS_DIR = Path(__file__).parent / "translations"

# Phone country code → language mapping
COUNTRY_LANG = {
    "54": "es",  # Argentina
    "52": "es",  # Mexico
    "34": "es",  # Spain
    "56": "es",  # Chile
    "57": "es",  # Colombia
    "51": "es",  # Peru
    "58": "es",  # Venezuela
    "53": "es",  # Cuba
    "591": "es", # Bolivia
    "593": "es", # Ecuador
    "595": "es", # Paraguay
    "598": "es", # Uruguay
    "503": "es", # El Salvador
    "504": "es", # Honduras
    "505": "es", # Nicaragua
    "506": "es", # Costa Rica
    "507": "es", # Panama
    "1": "en",   # US/Canada
    "44": "en",  # UK
    "61": "en",  # Australia
    "64": "en",  # NZ
    "91": "en",  # India (default to English)
    "55": "pt",  # Brazil
    "351": "pt", # Portugal
    "244": "pt", # Angola
    "258": "pt", # Mozambique
    "33": "fr",  # France
    "32": "fr",  # Belgium
    "41": "fr",  # Switzerland
    "225": "fr", # Côte d'Ivoire
    "221": "fr", # Senegal
    "237": "fr", # Cameroon
}

SUPPORTED_LANGS = {"en", "es", "pt", "fr"}
FALLBACK_LANG = "en"

# Cached translations
_translations: dict[str, dict] = {}


def load_translations():
    """Load all translation files into memory."""
    global _translations
    for lang in SUPPORTED_LANGS:
        path = TRANSLATIONS_DIR / f"{lang}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                _translations[lang] = json.load(f)
            logger.info(f"Loaded translations: {lang} ({len(_translations[lang])} keys)")
        else:
            logger.warning(f"No translation file for {lang}")
    logger.info(f"i18n initialized with languages: {list(_translations.keys())}")


def detect_language(phone: str) -> str:
    """Detect language from phone number country code.
    
    Args:
        phone: Phone number like '5491112345678@c.us' or '5491112345678'
    Returns:
        Language code: 'es', 'en', 'pt', 'fr'
    """
    # Remove @c.us suffix and +
    clean = phone.replace("@c.us", "").replace(".us", "").lstrip("+").strip()
    
    # Try to match country code (longest first)
    for code in sorted(COUNTRY_LANG.keys(), key=len, reverse=True):
        if clean.startswith(code):
            lang = COUNTRY_LANG[code]
            logger.debug(f"Phone {phone} → country {code} → lang {lang}")
            return lang
    
    logger.debug(f"Phone {phone} → no match → fallback {FALLBACK_LANG}")
    return FALLBACK_LANG


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate a key to the given language.
    
    Args:
        key: Dot-separated translation path (e.g., 'swap.start')
        lang: Language code
        **kwargs: Format variables
    
    Returns:
        Translated string, or key if not found
    """
    parts = key.split(".")
    translations = _translations.get(lang, _translations.get(FALLBACK_LANG, {}))
    
    value = translations
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part, None)
        else:
            value = None
            break
    
    if value is None:
        # Fallback to English
        en_translations = _translations.get("en", {})
        for part in parts:
            if isinstance(en_translations, dict):
                en_translations = en_translations.get(part, None)
            else:
                en_translations = None
                break
        value = en_translations
    
    if value is None:
        logger.warning(f"Missing translation: {key} ({lang})")
        return f"[{key}]"
    
    if isinstance(value, str) and kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, ValueError) as e:
            logger.warning(f"Format error in translation {key}: {e}")
            return value
    
    return value


def get_user_language(phone_hash: str, db_state: str | None = None, phone: str = "") -> str:
    """Get the language for a user. Priority: DB state > phone detection > fallback.
    
    Args:
        phone_hash: User's phone hash
        db_state: Language override from DB (if user set it manually)
        phone: Original phone number for auto-detection
    Returns:
        Language code
    """
    if db_state and db_state in SUPPORTED_LANGS:
        return db_state
    if phone:
        return detect_language(phone)
    return FALLBACK_LANG


def set_user_language(phone_hash: str, lang: str) -> bool:
    """Validate and set user language preference.
    Returns True if valid, False otherwise.
    """
    return lang in SUPPORTED_LANGS
