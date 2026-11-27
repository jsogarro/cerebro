import { useState, type FormEvent } from 'react';
import { isAxiosError } from 'axios';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { signIn } from '@/api/session';

function failureMessage(error: unknown): string {
    const detail = isAxiosError(error) ? error.response?.data?.detail : undefined;
    return typeof detail === 'string' ? detail : 'Sign-in failed. Check your details and try again.';
}

export function Login() {
    const navigate = useNavigate();
    const from = (useLocation().state as { from?: string } | null)?.from ?? '/app';
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(false);

    async function onSubmit(event: FormEvent) {
        event.preventDefault();
        setPending(true);
        setError(null);
        try {
            await signIn(email, password);
            navigate(from, { replace: true });
        } catch (caught) {
            setError(failureMessage(caught));
        } finally {
            setPending(false);
        }
    }

    return (
        <main className="flex min-h-dvh items-center justify-center bg-background px-4 text-foreground">
            <Card className="w-full max-w-sm shadow-sm">
                <CardHeader>
                    <h1 className="font-editorial text-2xl leading-none tracking-tight">Sign in</h1>
                </CardHeader>
                <CardContent>
                    <form className="space-y-4" onSubmit={onSubmit}>
                        <div className="space-y-2">
                            <label className="text-sm font-medium" htmlFor="login-email">Email</label>
                            <Input id="login-email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium" htmlFor="login-password">Password</label>
                            <Input id="login-password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
                        </div>
                        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
                        <Button type="submit" className="w-full" disabled={pending}>Sign in</Button>
                    </form>
                </CardContent>
            </Card>
        </main>
    );
}
