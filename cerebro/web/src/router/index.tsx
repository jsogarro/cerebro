import { lazy } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AppShell } from '@/components/layout/AppShell';
import { Landing } from '@/pages/Landing';
import { RouteError } from '@/components/common/RouteError';

// Lazy load feature pages
const Dashboard = lazy(() => import('@/pages/Dashboard').then(m => ({ default: m.Dashboard })));
const Research = lazy(() => import('@/pages/Research').then(m => ({ default: m.Research })));
const Agents = lazy(() => import('@/pages/Agents').then(m => ({ default: m.Agents })));
const Memory = lazy(() => import('@/pages/Memory').then(m => ({ default: m.Memory })));
const QA = lazy(() => import('@/pages/QA').then(m => ({ default: m.QA })));
const Costs = lazy(() => import('@/pages/Costs').then(m => ({ default: m.Costs })));
const Benchmarks = lazy(() => import('@/pages/Benchmarks').then(m => ({ default: m.Benchmarks })));
const Settings = lazy(() => import('@/pages/Settings').then(m => ({ default: m.Settings })));

export const router = createBrowserRouter([
    {
        path: '/',
        element: <Landing />,
        errorElement: <RouteError />,
    },
    {
        path: '/app',
        element: <AppShell />,
        errorElement: <RouteError />,
        children: [
            { index: true, element: <Navigate to="/app/dashboard" replace /> },
            { path: 'dashboard', element: <Dashboard /> },
            { path: 'research', element: <Research /> },
            { path: 'research/:id', element: <Research /> },
            { path: 'agents', element: <Agents /> },
            { path: 'memory', element: <Memory /> },
            { path: 'qa', element: <QA /> },
            { path: 'costs', element: <Costs /> },
            { path: 'benchmarks', element: <Benchmarks /> },
            { path: 'settings', element: <Settings /> },
        ],
    },
]);
