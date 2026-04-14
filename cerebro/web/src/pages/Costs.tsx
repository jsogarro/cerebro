import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { BarChart, DollarSign, ArrowUpRight } from "lucide-react";
import { useCostOverview } from "@/api/costs";

export function Costs() {
    const { data: costs, isLoading } = useCostOverview();

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Cost Optimization</h2>
                <p className="text-muted-foreground">Monitor and optimize agent architecture expenses.</p>
            </div>

            <div className="grid gap-6 md:grid-cols-3">
                <Card className="shadow-sm">
                    <CardHeader className="pb-2 flex flex-row items-center justify-between space-y-0">
                        <CardTitle className="text-sm font-medium">Daily Spend</CardTitle>
                        <DollarSign className="h-4 w-4 text-primary" />
                    </CardHeader>
                    <CardContent>
                        {isLoading ? (
                            <div className="h-8 w-24 bg-muted animate-pulse rounded"></div>
                        ) : (
                            <div className="space-y-1">
                                <div className="text-3xl font-bold font-mono">
                                    {costs?.currency === "USD" ? "$" : ""}{costs?.totalCostToday}
                                </div>
                                <div className="text-xs font-medium text-destructive flex items-center gap-1">
                                    <ArrowUpRight className="h-3 w-3" />
                                    {costs?.trend} from yesterday
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>

                <Card className="shadow-sm md:col-span-2">
                    <CardHeader>
                        <CardTitle className="text-base flex items-center gap-2">
                            <BarChart className="h-5 w-5 text-primary" />
                            Trend Analysis
                        </CardTitle>
                        <CardDescription>Provider breakdown over time</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="flex h-32 items-center justify-center rounded-lg border border-dashed bg-muted/20">
                            <span className="text-sm font-medium text-muted-foreground">Chart visual coming soon...</span>
                        </div>
                    </CardContent>
                </Card>
            </div>

            <Card className="shadow-sm">
                <CardHeader>
                    <CardTitle className="text-base">Breakdown by Provider</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="space-y-4">
                        <div className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/10">
                            <div>
                                <h4 className="font-semibold text-sm">OpenAI</h4>
                                <p className="text-xs text-muted-foreground font-mono">gpt-4-turbo</p>
                            </div>
                            <div className="flex items-center gap-4">
                                <div className="h-1.5 w-32 bg-secondary rounded-full overflow-hidden hidden sm:block">
                                    <div className="h-full bg-primary" style={{ width: '65%' }} />
                                </div>
                                <span className="font-bold font-mono text-sm uppercase">$80.00</span>
                            </div>
                        </div>
                        <div className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/10">
                            <div>
                                <h4 className="font-semibold text-sm">Anthropic</h4>
                                <p className="text-xs text-muted-foreground font-mono">claude-3-opus</p>
                            </div>
                            <div className="flex items-center gap-4">
                                <div className="h-1.5 w-32 bg-secondary rounded-full overflow-hidden hidden sm:block">
                                    <div className="h-full bg-primary" style={{ width: '35%' }} />
                                </div>
                                <span className="font-bold font-mono text-sm uppercase">$44.50</span>
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
