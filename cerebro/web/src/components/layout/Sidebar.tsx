import { NavLink } from "react-router-dom";
import {
    LayoutDashboard,
    FlaskConical,
    Network,
    Brain,
    ShieldCheck,
    DollarSign,
    BarChart2,
    Settings
} from "lucide-react";

import { ThemeToggle } from "./ThemeToggle";

const navigation = [
    { name: "Dashboard", href: "/app/dashboard", icon: LayoutDashboard },
    { name: "Research", href: "/app/research", icon: FlaskConical },
    { name: "Agents", href: "/app/agents", icon: Network },
    { name: "Memory", href: "/app/memory", icon: Brain },
    { name: "QA", href: "/app/qa", icon: ShieldCheck },
    { name: "Costs", href: "/app/costs", icon: DollarSign },
    { name: "Benchmarks", href: "/app/benchmarks", icon: BarChart2 },
];

export function Sidebar({ collapsed = false }: { collapsed?: boolean }) {
    if (collapsed) return null; // Placeholder for responsive collapse

    return (
        <div className="flex h-full w-64 flex-col border-r bg-card/50 backdrop-blur z-20">
            {/* Header / Logo */}
            <div className="flex h-14 items-center border-b px-4">
                <span className="text-xl font-bold text-primary flex items-center gap-2">
                    <Brain className="h-6 w-6" />
                    Cerebro
                </span>
            </div>

            {/* Nav Links */}
            <div className="flex-1 overflow-auto py-4">
                <nav aria-label="Primary navigation" className="space-y-1 px-2">
                    {navigation.map((item) => (
                        <NavLink
                            key={item.name}
                            to={item.href}
                            className={({ isActive }) =>
                                `group flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${isActive
                                    ? "bg-primary/10 text-primary"
                                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                }`
                            }
                        >
                            <item.icon className="mr-3 h-5 w-5 flex-shrink-0" />
                            {item.name}
                        </NavLink>
                    ))}
                </nav>
            </div>

            {/* Footer / Settings */}
            <div className="border-t p-4 flex flex-col gap-4">
                <NavLink
                    to="/app/settings"
                    className={({ isActive }) =>
                        `group flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${isActive
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground"
                        }`
                    }
                >
                    <Settings className="mr-3 h-5 w-5 flex-shrink-0" />
                    Settings
                </NavLink>
                <div className="flex items-center justify-between px-3">
                    <span className="text-sm font-medium text-muted-foreground">Theme</span>
                    <ThemeToggle />
                </div>
            </div>
        </div>
    );
}
