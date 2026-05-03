import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useThemeStore } from "@/stores/themeStore";

export function ThemeToggle() {
    const toggleTheme = useThemeStore((state) => state.toggleTheme);

    return (
        <Button aria-label="Toggle color theme" variant="ghost" size="icon" onClick={toggleTheme} className="rounded-full">
            <Sun aria-hidden="true" className="h-[1.2rem] w-[1.2rem] rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
            <Moon aria-hidden="true" className="absolute h-[1.2rem] w-[1.2rem] rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
            <span className="sr-only">Toggle theme</span>
        </Button>
    );
}
