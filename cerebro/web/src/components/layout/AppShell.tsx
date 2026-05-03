import { useState, Suspense } from 'react';
import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Header } from './Header';
import { PageLoading } from '@/components/common/Loading';

export function AppShell() {
    const [sidebarOpen, setSidebarOpen] = useState(false);

    return (
        <div className="flex h-screen w-full bg-background text-foreground overflow-hidden">
            {/* Desktop Sidebar */}
            <div className="hidden md:block">
                <Sidebar />
            </div>

            {/* Mobile Sidebar Overlay */}
            {sidebarOpen && (
                <button
                    type="button"
                    aria-label="Close navigation menu"
                    className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm md:hidden"
                    onClick={() => setSidebarOpen(false)}
                />
            )}

            {/* Mobile Sidebar */}
            {sidebarOpen && (
                <div
                    aria-label="Navigation menu"
                    aria-modal="true"
                    className="fixed inset-y-0 left-0 z-50 w-64 transform transition-transform duration-200 ease-in-out md:hidden"
                    role="dialog"
                >
                    <Sidebar />
                </div>
            )}

            <div className="flex-1 flex flex-col min-w-0">
                <Header toggleSidebar={() => setSidebarOpen(true)} />
                <main id="main-content" className="flex-1 overflow-auto p-4 md:p-6 pb-20 md:pb-6 relative">
                    <Suspense fallback={<PageLoading />}>
                        <Outlet />
                    </Suspense>
                </main>
            </div>
        </div>
    );
}
