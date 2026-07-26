import type { Ref } from "react";
import { NavLink } from "react-router-dom";
import {
    BarChart2,
    Brain,
    DollarSign,
    FileClock,
    FlaskConical,
    LayoutDashboard,
    Network,
    ScrollText,
    Settings,
    ShieldCheck,
} from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";

const primaryNavigation = [
    { name: "Workflows", href: "/app/workflows", icon: ScrollText, marker: "01" },
    { name: "Runs", href: "/app/runs", icon: FileClock, marker: "02" },
];

const legacyNavigation = [
    { name: "Dashboard", href: "/app/dashboard", icon: LayoutDashboard },
    { name: "Research", href: "/app/research", icon: FlaskConical },
    { name: "Agents", href: "/app/agents", icon: Network },
    { name: "Memory", href: "/app/memory", icon: Brain },
    { name: "QA", href: "/app/qa", icon: ShieldCheck },
    { name: "Costs", href: "/app/costs", icon: DollarSign },
    { name: "Benchmarks", href: "/app/benchmarks", icon: BarChart2 },
];

type SidebarProps = {
    collapsed?: boolean;
    onNavigate?: () => void;
    primaryLinkRef?: Ref<HTMLAnchorElement>;
};

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `group flex items-center rounded-sm border-l-2 px-3 py-2.5 text-sm transition-colors ${
        isActive
            ? "border-accent bg-primary/10 font-semibold text-primary"
            : "border-transparent text-muted-foreground hover:border-border hover:bg-muted/70 hover:text-foreground"
    }`;

export function Sidebar({ collapsed = false, onNavigate, primaryLinkRef }: SidebarProps) {
    if (collapsed) return null;

    return (
        <div className="workbench-sidebar z-20 flex h-full w-64 flex-col border-r">
            <div className="flex min-h-[4.5rem] items-center border-b px-5">
                <div className="flex items-center gap-3 text-primary">
                    <span className="flex h-9 w-9 items-center justify-center rounded-full border border-primary/25 bg-primary/10">
                        <Brain aria-hidden="true" className="h-5 w-5" />
                    </span>
                    <span>
                        <span className="font-editorial block text-xl leading-none tracking-[-0.025em]">Cerebro</span>
                        <span className="mt-1 block text-[0.61rem] font-semibold uppercase tracking-[0.17em] text-muted-foreground">
                            Research systems
                        </span>
                    </span>
                </div>
            </div>

            <div className="flex-1 overflow-auto px-3 py-5">
                <nav aria-label="Primary navigation">
                    <p className="workbench-nav-label px-3">Workbench</p>
                    <div className="mt-2 space-y-1">
                        {primaryNavigation.map((item, index) => (
                            <NavLink
                                key={item.name}
                                ref={index === 0 ? primaryLinkRef : undefined}
                                to={item.href}
                                className={navLinkClass}
                                onClick={onNavigate}
                            >
                                <span
                                    aria-hidden="true"
                                    className="mr-3 font-mono text-[0.62rem] tracking-wider text-muted-foreground group-aria-[current=page]:text-accent"
                                >
                                    {item.marker}
                                </span>
                                <item.icon aria-hidden="true" className="mr-2.5 h-4 w-4 flex-shrink-0" />
                                {item.name}
                            </NavLink>
                        ))}
                    </div>
                </nav>

                <div className="my-5 border-t border-border/80" />

                <nav aria-label="Legacy and developer navigation">
                    <p className="workbench-nav-label px-3">Legacy &amp; developer</p>
                    <div className="mt-2 space-y-0.5">
                        {legacyNavigation.map((item) => (
                            <NavLink
                                key={item.name}
                                to={item.href}
                                className={navLinkClass}
                                onClick={onNavigate}
                            >
                                <item.icon aria-hidden="true" className="mr-3 h-4 w-4 flex-shrink-0" />
                                {item.name}
                            </NavLink>
                        ))}
                    </div>
                </nav>
            </div>

            <div className="flex flex-col gap-3 border-t bg-background/45 p-3">
                <NavLink
                    to="/app/settings"
                    className={navLinkClass}
                    onClick={onNavigate}
                >
                    <Settings aria-hidden="true" className="mr-3 h-4 w-4 flex-shrink-0" />
                    Settings
                </NavLink>
                <div className="flex items-center justify-between border-t border-border/70 px-3 pt-3">
                    <span className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Theme</span>
                    <ThemeToggle />
                </div>
            </div>
        </div>
    );
}
