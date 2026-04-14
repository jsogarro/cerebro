import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ShieldCheck, Server, AlertTriangle, FileCheck } from "lucide-react";
import { useQaMetrics } from "@/api/qa";

export function QA() {
    const { data: metrics, isLoading } = useQaMetrics();

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Quality Assurance</h2>
                <p className="text-muted-foreground">Monitor fact-checking and validation results.</p>
            </div>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
                {isLoading ? (
                    <div className="md:col-span-full animate-pulse h-12 bg-muted rounded"></div>
                ) : (
                    metrics?.map((metric: any) => (
                        <Card key={metric.id} className="shadow-sm">
                            <CardHeader className="pb-2 flex flex-row items-center justify-between space-y-0">
                                <CardTitle className="text-sm font-medium">{metric.title}</CardTitle>
                                {metric.status === 'healthy' ? (
                                    <ShieldCheck className="h-4 w-4 text-emerald-500" />
                                ) : (
                                    <AlertTriangle className="h-4 w-4 text-amber-500" />
                                )}
                            </CardHeader>
                            <CardContent>
                                <div className="text-3xl font-bold font-mono">{metric.value}</div>
                                <div className="text-xs text-muted-foreground mt-1 capitalize font-medium">{metric.status}</div>
                            </CardContent>
                        </Card>
                    ))
                )}
            </div>

            <Card className="shadow-sm">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <FileCheck className="h-5 w-5 text-primary" />
                        Recent Validations
                    </CardTitle>
                    <CardDescription>Execution traces over the last 24h</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="space-y-4">
                        <div className="flex items-center justify-between rounded-lg border p-4 bg-muted/10 hover:bg-muted/30 transition-colors">
                            <div className="flex items-center gap-4">
                                <Server className="h-5 w-5 text-primary/50" />
                                <div>
                                    <p className="font-medium text-sm">Validation Run #1042</p>
                                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-mono mt-0.5">Citation Check</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-4">
                                <div className="text-xs font-medium text-muted-foreground hidden sm:block">2 mins ago</div>
                                <Badge variant="outline" className="text-emerald-500 border-emerald-500/20 bg-emerald-500/10">Passed</Badge>
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
