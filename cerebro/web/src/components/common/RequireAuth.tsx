import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { getAccessToken } from '@/api/auth';

export function RequireAuth() {
    const { pathname, search } = useLocation();
    if (getAccessToken()) return <Outlet />;
    return <Navigate to="/login" replace state={{ from: pathname + search }} />;
}
