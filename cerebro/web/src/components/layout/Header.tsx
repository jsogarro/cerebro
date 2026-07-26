import { Bell, Menu } from "lucide-react";
import { matchPath, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { DialogTrigger } from "@/components/ui/dialog";

type PageContext = {
    eyebrow: string;
    title: string;
};

const routeContexts: Array<{ path: string; context: PageContext }> = [
    { path: "/app/workflows", context: { eyebrow: "Research workbench", title: "Workflows" } },
    { path: "/app/runs", context: { eyebrow: "Research workbench", title: "Runs" } },
    { path: "/app/research/:id", context: { eyebrow: "Legacy research", title: "Research project" } },
    { path: "/app/dashboard", context: { eyebrow: "Legacy overview", title: "Dashboard" } },
    { path: "/app/research", context: { eyebrow: "Legacy research", title: "Research" } },
    { path: "/app/agents", context: { eyebrow: "Developer tool", title: "Agents" } },
    { path: "/app/memory", context: { eyebrow: "Developer tool", title: "Memory" } },
    { path: "/app/qa", context: { eyebrow: "Developer tool", title: "Quality assurance" } },
    { path: "/app/costs", context: { eyebrow: "Developer tool", title: "Costs" } },
    { path: "/app/benchmarks", context: { eyebrow: "Developer tool", title: "Benchmarks" } },
    { path: "/app/settings", context: { eyebrow: "Workspace", title: "Settings" } },
];

function contextForPath(pathname: string): PageContext {
    return routeContexts.find(({ path }) => matchPath({ path, end: true }, pathname))?.context ?? {
        eyebrow: "Research workbench",
        title: "Cerebro",
    };
}

export function Header() {
    const { pathname } = useLocation();
    const pageContext = contextForPath(pathname);

    return (
        <header className="workbench-header z-10 flex min-h-16 items-center justify-between border-b px-3 sm:px-4 md:min-h-[4.5rem] md:px-8 lg:px-10">
            <div className="flex min-w-0 items-center gap-3 md:gap-4">
                <DialogTrigger asChild>
                    <Button aria-label="Open navigation menu" variant="ghost" size="icon" className="shrink-0 md:hidden">
                        <Menu aria-hidden="true" className="h-5 w-5" />
                    </Button>
                </DialogTrigger>
                <div className="min-w-0 border-l border-border/80 pl-3 md:border-l-0 md:pl-0">
                    <p className="workbench-kicker truncate">{pageContext.eyebrow}</p>
                    <h1 className="font-editorial truncate text-xl leading-tight tracking-[-0.02em] md:text-2xl">
                        {pageContext.title}
                    </h1>
                </div>
            </div>
            <div className="flex items-center gap-2 md:gap-3">
                <Button aria-label="View notifications" variant="ghost" size="icon">
                    <Bell aria-hidden="true" className="h-5 w-5" />
                </Button>
                <div
                    aria-label="Current user: Cerebro researcher"
                    className="flex h-9 w-9 items-center justify-center rounded-full border border-primary/30 bg-primary/10 font-mono text-[0.7rem] font-semibold tracking-[0.08em] text-primary"
                    role="img"
                >
                    CR
                </div>
            </div>
        </header>
    );
}
