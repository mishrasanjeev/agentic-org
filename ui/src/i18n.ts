/**
 * react-i18next configuration for AgenticOrg.
 *
 * Loads translations from the locales directory with English as the
 * default / fallback language.  Hindi is the first additional language.
 *
 * Usage in components:
 *   import { useTranslation } from "react-i18next";
 *   const { t } = useTranslation();
 *   <span>{t("nav.dashboard")}</span>
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import hi from "./locales/hi.json";

const STORAGE_KEY = "agenticorg_lang";

// Retrieve persisted language preference (default: English)
function getInitialLanguage(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && ["en", "hi"].includes(stored)) {
      return stored;
    }
  } catch {
    // localStorage may be unavailable (SSR, privacy mode)
  }
  return "en";
}

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    hi: { translation: hi },
  },
  lng: getInitialLanguage(),
  fallbackLng: "en",
  interpolation: {
    escapeValue: false, // React already escapes
  },
  react: {
    useSuspense: false, // avoid Suspense flash for translations
  },
});

// Persist language changes
i18n.on("languageChanged", (lng: string) => {
  try {
    localStorage.setItem(STORAGE_KEY, lng);
    document.documentElement.lang = lng;
  } catch {
    // ignore
  }
});

export default i18n;
