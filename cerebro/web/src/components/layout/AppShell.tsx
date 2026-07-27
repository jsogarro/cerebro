import { Suspense, useRef, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { PageLoading } from '@/components/common/Loading';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogTitle,
} from '@/components/ui/dialog';
import { Header } from './Header';
import { Sidebar } from './Sidebar';

export function AppShell() {
    const [sidebarOpen, setSidebarOpen] = useState(false);
    const mobileInitialFocusRef = useRef<HTMLAnchorElement>(null);

    return (
        <Dialog open={sidebarOpen} onOpenChange={setSidebarOpen}>
            <div className="workbench-shell flex h-dvh w-full overflow-hidden bg-background text-foreground">
                <a className="skip-link" href="#main-content">
                    Skip to main content
                </a>

                <aside aria-label="Workbench sidebar" className="hidden md:block">
                    <Sidebar />
                </aside>

                <div className="flex min-w-0 flex-1 flex-col">
                    <Header />
                    <main
                        id="main-content"
                        aria-label="Main content"
                        className="workbench-main relative flex-1 overflow-auto px-4 py-5 pb-20 md:px-8 md:py-7 md:pb-8 lg:px-10"
                        tabIndex={0}
                    >
                        <Suspense fallback={<PageLoading />}>
                            <Outlet />
                        </Suspense>
                    </main>
                </div>
            </div>

            <DialogContent
                className="left-0 top-0 h-dvh w-[min(20rem,calc(100vw-1.5rem))] max-w-none translate-x-0 translate-y-0 gap-0 overflow-hidden rounded-none border-y-0 border-l-0 border-r bg-background p-0 shadow-2xl data-[state=closed]:slide-out-to-left-full data-[state=open]:slide-in-from-left-full sm:rounded-none"
                onOpenAutoFocus={(event) => {
                    event.preventDefault();
                    mobileInitialFocusRef.current?.focus();
                }}
            >
                <DialogTitle className="sr-only">Navigation menu</DialogTitle>
                <DialogDescription className="sr-only">
                    Navigate between the research workbench and legacy developer tools.
                </DialogDescription>
                <Sidebar
                    onNavigate={() => setSidebarOpen(false)}
                    primaryLinkRef={mobileInitialFocusRef}
                />
            </DialogContent>
        </Dialog>
    );
}
