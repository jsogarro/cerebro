import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Settings as SettingsIcon, Key } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function Settings() {
    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">System Settings</h2>
                <p className="text-muted-foreground">Configure global preferences and API keys.</p>
            </div>

            <div className="grid gap-6">
                <Card className="shadow-sm max-w-2xl">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Key className="h-4 w-4 text-primary" />
                            API Configuration
                        </CardTitle>
                        <CardDescription>Provider keys for AI models.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">OpenAI API Key</label>
                            <Input type="password" placeholder="sk-..." value="****************" readOnly />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Anthropic API Key</label>
                            <Input type="password" placeholder="sk-ant-..." />
                        </div>
                        <Button className="mt-4">Save Changes</Button>
                    </CardContent>
                </Card>

                <Card className="shadow-sm max-w-2xl">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <SettingsIcon className="h-4 w-4 text-primary" />
                            General Preferences
                        </CardTitle>
                        <CardDescription>Customize application behavior.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="p-3 border rounded-lg flex items-center justify-between">
                            <div>
                                <h4 className="font-semibold text-sm">Strict Rate Limiting</h4>
                                <p className="text-xs text-muted-foreground">Enforce conservative RPM limits globally.</p>
                            </div>
                            <button
                                aria-checked="true"
                                aria-label="Toggle strict rate limiting"
                                className="h-5 w-9 bg-primary/20 rounded-full flex items-center px-0.5 cursor-pointer"
                                role="switch"
                                type="button"
                            >
                                <span className="h-4 w-4 rounded-full bg-primary shadow translate-x-4" />
                            </button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
