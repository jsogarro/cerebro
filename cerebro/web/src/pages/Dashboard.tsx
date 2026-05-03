import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Activity, Beaker, Users, DollarSign, Clock, CheckCircle2, AlertCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";

const recentResearch = [
    { id: "RES-104", title: "Q1 Market Analysis", status: "running", progress: 78, time: "2m ago" },
    { id: "RES-103", title: "Competitor Tech Stack", status: "completed", progress: 100, time: "1h ago" },
    { id: "RES-102", title: "Customer Sentiment", status: "failed", progress: 45, time: "3h ago" },
    { id: "RES-101", title: "Supply Chain Optimization", status: "completed", progress: 100, time: "1d ago" },
];

const activityFeed = [
    { id: 1, text: "Agent Alpha discovered 14 new sources for Q1 Market Analysis", time: "2m ago", type: "info" },
    { id: 2, text: "Agent Gamma completed validation of Competitor Tech Stack", time: "1h ago", type: "success" },
    { id: 3, text: "Agent Beta encountered API rate limit in Customer Sentiment", time: "3h ago", type: "error" },
    { id: 4, text: "Research protocol Supply Chain Optimization finalized", time: "1d ago", type: "success" },
];

export function Dashboard() {
    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <h2 className="text-3xl font-bold tracking-tight">Dashboard Overview</h2>

            {/* Stats row */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <Card className="hover:border-primary/50 transition-colors shadow-sm">
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 text-muted-foreground pb-2">
                        <CardTitle className="text-sm font-medium text-foreground">Active Research</CardTitle>
                        <Beaker className="h-4 w-4 text-primary" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold">12</div>
                        <p className="text-xs text-muted-foreground mt-1 font-medium">+2 from yesterday</p>
                    </CardContent>
                </Card>
                <Card className="hover:border-primary/50 transition-colors shadow-sm">
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 text-muted-foreground pb-2">
                        <CardTitle className="text-sm font-medium text-foreground">Agents Online</CardTitle>
                        <Users className="h-4 w-4 text-primary" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold">48</div>
                        <p className="text-xs text-muted-foreground mt-1 font-medium">Across 5 specialized pools</p>
                    </CardContent>
                </Card>
                <Card className="hover:border-primary/50 transition-colors shadow-sm">
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 text-muted-foreground pb-2">
                        <CardTitle className="text-sm font-medium text-foreground">System Health</CardTitle>
                        <Activity className="h-4 w-4 text-emerald-500" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold tracking-tight text-emerald-700 dark:text-emerald-300">99.9%</div>
                        <p className="text-xs text-muted-foreground mt-1 font-medium">All services operational</p>
                    </CardContent>
                </Card>
                <Card className="hover:border-primary/50 transition-colors shadow-sm">
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 text-muted-foreground pb-2">
                        <CardTitle className="text-sm font-medium text-foreground">Total Cost Today</CardTitle>
                        <DollarSign className="h-4 w-4 text-primary" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold">$124.50</div>
                        <p className="text-xs text-muted-foreground mt-1 font-medium">+14% from yesterday</p>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-7">
                {/* Recent Research */}
                <Card className="col-span-1 lg:col-span-4 flex flex-col shadow-sm">
                    <CardHeader>
                        <CardTitle>Recent Research</CardTitle>
                    </CardHeader>
                    <CardContent className="flex-1">
                        <div className="space-y-6">
                            {recentResearch.map((item) => (
                                <div key={item.id} className="flex items-center justify-between group">
                                    <div className="flex items-center gap-4">
                                        <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center group-hover:bg-primary/20 transition-colors border border-primary/10">
                                            <Beaker className="h-5 w-5 text-primary" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-semibold leading-none mb-1.5">{item.title}</p>
                                            <div className="flex items-center text-xs text-muted-foreground gap-2 font-medium">
                                                <span className="font-mono bg-muted px-1 rounded">{item.id}</span>
                                                <span className="text-border">•</span>
                                                <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> {item.time}</span>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-4">
                                        <div className="w-24 hidden sm:block">
                                            <div className="h-2 w-full bg-secondary rounded-full overflow-hidden">
                                                <div
                                                    className={`h-full transition-all duration-1000 ${item.status === 'completed' ? 'bg-emerald-500' : item.status === 'failed' ? 'bg-destructive' : 'bg-primary'}`}
                                                    style={{ width: `${item.progress}%` }}
                                                />
                                            </div>
                                        </div>
                                        <Badge variant={item.status === 'completed' ? 'default' : item.status === 'failed' ? 'destructive' : 'secondary'} className={`capitalize w-24 justify-center ${item.status === 'completed' ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/20 shadow-none border-none pointer-events-none' : ''}`}>
                                            {item.status}
                                        </Badge>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>

                {/* Live Activity Feed */}
                <Card className="col-span-1 lg:col-span-3 flex flex-col shadow-sm">
                    <CardHeader>
                        <CardTitle>Live Activity Stream</CardTitle>
                    </CardHeader>
                    <CardContent className="flex-1">
                        <div className="space-y-6 relative before:absolute before:inset-0 before:ml-[9px] before:-translate-x-px md:before:mx-auto md:before:translate-x-0 before:h-full before:w-0.5 before:bg-gradient-to-b before:from-transparent before:via-border before:to-transparent">
                            {activityFeed.map((activity) => (
                                <div key={activity.id} className="relative flex items-center justify-between md:justify-normal md:odd:flex-row-reverse group is-active">
                                    <div className="flex items-center justify-center w-5 h-5 rounded-full border border-background bg-muted text-muted-foreground shrink-0 md:order-1 md:group-odd:-translate-x-1/2 md:group-even:translate-x-1/2 z-10 shadow-sm">
                                        {activity.type === 'success' && <CheckCircle2 className="h-3 w-3 text-emerald-500" />}
                                        {activity.type === 'info' && <div className="h-2 w-2 rounded-full bg-primary" />}
                                        {activity.type === 'error' && <AlertCircle className="h-3 w-3 text-destructive" />}
                                    </div>
                                    <div className="w-[calc(100%-2.5rem)] md:w-[calc(50%-1.5rem)] p-3 rounded-lg border bg-card shadow-sm transition-all hover:shadow-md">
                                        <div className="flex items-center justify-between mb-1">
                                            <span className="font-mono text-[10px] text-muted-foreground/70 uppercase tracking-wider">{activity.type}</span>
                                            <span className="text-[10px] text-muted-foreground tabular-nums flex items-center gap-1"><Clock className="h-2.5 w-2.5" />{activity.time}</span>
                                        </div>
                                        <div className="text-sm font-medium text-foreground/90">{activity.text}</div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
