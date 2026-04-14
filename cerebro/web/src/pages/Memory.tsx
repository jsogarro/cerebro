import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Search, Brain, Clock, Database } from "lucide-react";
import { useMemoryNodes } from "@/api/memory";

export function Memory() {
    const { data: nodes, isLoading } = useMemoryNodes();

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Memory Explorer</h2>
                    <p className="text-muted-foreground">Search and visualize agent context and learned parameters.</p>
                </div>
            </div>

            <div className="flex items-center gap-2 max-w-md">
                <Search className="h-4 w-4 text-muted-foreground absolute ml-3" />
                <Input
                    placeholder="Search memories by content or ID..."
                    className="pl-9"
                />
            </div>

            <div className="grid gap-6 md:grid-cols-3">
                <Card className="md:col-span-2 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base flex items-center gap-2">
                            <Database className="h-4 w-4 text-primary" />
                            Recent Nodes
                        </CardTitle>
                        <CardDescription>Knowledge base entries</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="space-y-4">
                            {isLoading ? (
                                <p className="text-sm text-muted-foreground animate-pulse">Loading nodes...</p>
                            ) : (
                                nodes?.map((node: any) => (
                                    <div key={node.id} className="flex items-start gap-4 p-4 border rounded-xl hover:bg-muted/30 transition-colors">
                                        <div className="h-10 w-10 shrink-0 rounded-full bg-primary/10 flex items-center justify-center">
                                            <Brain className="h-5 w-5 text-primary" />
                                        </div>
                                        <div className="flex-1">
                                            <div className="flex items-center justify-between">
                                                <h4 className="font-medium">{node.content}</h4>
                                                <Badge variant="outline" className="text-xs">{node.type}</Badge>
                                            </div>
                                            <div className="flex items-center gap-2 text-xs text-muted-foreground mt-2">
                                                <span className="font-mono bg-muted px-1 rounded">{node.id}</span>
                                                <span>•</span>
                                                <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> {node.timestamp}</span>
                                            </div>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                    </CardContent>
                </Card>

                <Card className="shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base">Memory Stats</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex justify-between items-center p-3 bg-muted/50 rounded-lg">
                            <span className="text-sm font-medium">Total Nodes</span>
                            <span className="text-xl font-bold font-mono">1,204</span>
                        </div>
                        <div className="flex justify-between items-center p-3 bg-muted/50 rounded-lg">
                            <span className="text-sm font-medium">Edges</span>
                            <span className="text-xl font-bold font-mono">3,492</span>
                        </div>
                        <div className="flex justify-between items-center p-3 bg-muted/50 rounded-lg">
                            <span className="text-sm font-medium">Storage Size</span>
                            <span className="text-xl font-bold font-mono">42 MB</span>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
