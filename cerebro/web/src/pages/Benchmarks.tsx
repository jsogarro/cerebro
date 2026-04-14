import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PlayCircle, Award, Target, LayoutTemplate } from "lucide-react";
import { useBenchmarks } from "@/api/benchmarks";

export function Benchmarks() {
    const { data: benchmarks, isLoading } = useBenchmarks();

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Performance Benchmarks</h2>
                    <p className="text-muted-foreground">Compare multi-agent system performance against established baselines.</p>
                </div>
                <Button className="gap-2">
                    <PlayCircle className="h-4 w-4" /> Run Benchmark
                </Button>
            </div>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {isLoading ? (
                    <div className="col-span-full h-24 animate-pulse bg-muted rounded-xl"></div>
                ) : (
                    benchmarks?.map((bm: any) => (
                        <Card key={bm.id} className="shadow-sm hover:border-primary/50 transition-colors">
                            <CardHeader className="pb-3">
                                <CardTitle className="text-base flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <Award className="h-4 w-4 text-primary" />
                                        {bm.name}
                                    </div>
                                </CardTitle>
                                <CardDescription className="text-xs font-mono">{bm.id}</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="space-y-4">
                                    <div className="flex items-baseline justify-between">
                                        <div>
                                            <p className="text-xs text-muted-foreground mb-1">Current Score</p>
                                            <div className="text-3xl font-bold font-mono text-emerald-600 dark:text-emerald-400">{bm.score}</div>
                                        </div>
                                        <div className="text-right">
                                            <p className="text-xs text-muted-foreground mb-1">Baseline</p>
                                            <div className="text-sm font-semibold font-mono">{bm.baseline}</div>
                                        </div>
                                    </div>
                                    <div>
                                        <div className="h-2 w-full bg-secondary rounded-full overflow-hidden flex">
                                            <div className="h-full bg-muted-foreground transition-all" style={{ width: bm.baseline }} />
                                        </div>
                                        <div className="h-2 w-full bg-secondary rounded-full overflow-hidden flex -mt-2">
                                            <div className="h-full bg-emerald-500/80 transition-all opacity-80 backdrop-blur-sm" style={{ width: bm.score }} />
                                        </div>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    ))
                )}

                <Card className="shadow-sm border-dashed bg-muted/20 flex flex-col items-center justify-center py-8 hover:bg-muted/40 transition-colors cursor-pointer">
                    <LayoutTemplate className="h-8 w-8 text-muted-foreground mb-2" />
                    <p className="text-sm font-medium">Add New Suite</p>
                    <p className="text-xs text-muted-foreground mt-1">Import HuggingFace config</p>
                </Card>
            </div>

            <Card className="shadow-sm">
                <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                        <Target className="h-5 w-5 text-primary" />
                        Detailed Evaluation Report
                    </CardTitle>
                    <CardDescription>Comparison across models over the last 30 days</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="h-40 flex items-center justify-center rounded-lg border border-dashed bg-muted/10">
                        <span className="text-sm font-medium text-muted-foreground">Chart visual coming soon...</span>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
