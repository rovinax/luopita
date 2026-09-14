export type ThemeMode = "system" | "light" | "dark";

const KEY = "luopita_theme";

export function readTheme(): ThemeMode {
  try {
    const value = localStorage.getItem(KEY);
    if (value === "light" || value === "dark" || value === "system") {
      return value;
    }
  } catch {
    /* ignore */
  }
  return "system";
}

export function applyTheme(mode: ThemeMode): void {
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") {
    root.setAttribute("data-theme", mode);
  } else {
    root.removeAttribute("data-theme");
  }
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* ignore */
  }
}
