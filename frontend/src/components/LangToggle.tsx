import { useLang } from "../i18n";

export default function LangToggle() {
  const { lang, toggle, t } = useLang();
  return (
    <button
      type="button"
      className="lang-toggle"
      onClick={toggle}
      aria-label={t("langToggleA11y")}
      title={t("langToggleA11y")}
    >
      <span className={lang === "en" ? "on" : ""}>EN</span>
      <span className="bar" />
      <span className={lang === "zh" ? "on" : ""}>中</span>
    </button>
  );
}
