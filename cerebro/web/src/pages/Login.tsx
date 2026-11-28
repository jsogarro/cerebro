import { useRef, useState, type FormEvent } from 'react';
import { isAxiosError } from 'axios';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { apiErrorMessage } from '@/api/client';
import { signIn } from '@/api/session';

function failureMessage(error: unknown): string {
    return apiErrorMessage(error) ?? 'Sign-in failed. Check your details and try again.';
}

function isMfaChallenge(error: unknown, message: string): boolean {
    return isAxiosError(error) && error.response?.status === 401 && /mfa/i.test(message);
}

export function Login() {
    const navigate = useNavigate();
    const rawFrom = (useLocation().state as { from?: unknown } | null)?.from;
    const from = typeof rawFrom === 'string' && rawFrom.startsWith('/app') ? rawFrom : '/app';
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [totpCode, setTotpCode] = useState('');
    const [mfaRequired, setMfaRequired] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(false);
    const totpInputRef = useRef<HTMLInputElement>(null);

    async function onSubmit(event: FormEvent) {
        event.preventDefault();
        setPending(true);
        setError(null);
        try {
            await signIn(email, password, totpCode);
            navigate(from, { replace: true });
        } catch (caught) {
            const message = failureMessage(caught);
            setError(message);
            if (isMfaChallenge(caught, message)) {
                setMfaRequired(true);
                totpInputRef.current?.focus();
            }
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
                        <div className="space-y-2">
                            <label className="text-sm font-medium" htmlFor="login-totp">
                                Authenticator or recovery code
                                {!mfaRequired ? (
                                    <span className="font-normal text-muted-foreground"> (if enabled)</span>
                                ) : null}
                            </label>
                            <Input
                                ref={totpInputRef}
                                id="login-totp"
                                type="text"
                                inputMode="text"
                                autoComplete="one-time-code"
                                maxLength={32}
                                required={mfaRequired}
                                aria-describedby="login-totp-hint"
                                value={totpCode}
                                onChange={(e) => setTotpCode(e.target.value)}
                            />
                            <p id="login-totp-hint" className="text-xs text-muted-foreground">
                                Enter a 6-digit authenticator code or a recovery code if two-factor authentication is enabled.
                            </p>
                        </div>
                        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
                        <Button type="submit" className="w-full" disabled={pending}>Sign in</Button>
                    </form>
                </CardContent>
            </Card>
        </main>
    );
}
