import { Menu, Bell } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Header({ toggleSidebar }: { toggleSidebar: () => void }) {
    return (
        <header className="h-14 border-b flex items-center justify-between px-4 bg-background z-10 sticky top-0 shadow-sm">
            <div className="flex items-center gap-4">
                <Button variant="ghost" size="icon" className="md:hidden" onClick={toggleSidebar}>
                    <Menu className="h-5 w-5" />
                    <span className="sr-only">Toggle Sidebar</span>
                </Button>
                {/* We can use useLocation to display breadcrumbs dynamically later */}
                <h1 className="font-semibold text-lg hidden md:block">Dashboard</h1>
            </div>
            <div className="flex items-center gap-4">
                <Button variant="ghost" size="icon" className="relative">
                    <Bell className="h-5 w-5" />
                    <span className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-primary" />
                    <span className="sr-only">Notifications</span>
                </Button>
                <div className="h-8 w-8 rounded-full bg-primary/20 flex items-center justify-center font-semibold text-primary overflow-hidden">
                    <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=cerebro" alt="Avatar" />
                </div>
            </div>
        </header>
    );
}
