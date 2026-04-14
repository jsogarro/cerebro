import { create } from 'zustand';

interface AuthState {
    isAuthenticated: boolean;
    user: null | { id: string; name: string; email: string };
    setAuthenticated: (value: boolean) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
    isAuthenticated: true, // Initially true for local development
    user: null,
    setAuthenticated: (value) => set({ isAuthenticated: value }),
}));
