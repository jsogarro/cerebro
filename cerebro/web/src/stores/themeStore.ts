import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type Theme = 'light' | 'dark' | 'system';

interface ThemeState {
    theme: Theme;
    resolvedTheme: 'light' | 'dark';
    setTheme: (theme: Theme) => void;
    toggleTheme: () => void;
    applyTheme: () => void;
}

export const useThemeStore = create<ThemeState>()(
    persist(
        (set, get) => ({
            theme: 'system',
            resolvedTheme: 'light',

            setTheme: (theme) => {
                set({ theme });
                get().applyTheme();
            },

            toggleTheme: () => {
                const current = get().resolvedTheme;
                const next = current === 'light' ? 'dark' : 'light';
                set({ theme: next, resolvedTheme: next });
                get().applyTheme();
            },

            applyTheme: () => {
                const { theme, resolvedTheme } = get();
                const root = document.documentElement;

                if (theme === 'system') {
                    const systemTheme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
                    set({ resolvedTheme: systemTheme });
                    root.classList.toggle('dark', systemTheme === 'dark');
                } else {
                    root.classList.toggle('dark', resolvedTheme === 'dark');
                }
            },
        }),
        {
            name: 'cerebro-theme',
        }
    )
);
