import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Plus, Search, Beaker, Clock, ArrowLeft, Terminal, FileText, Settings as SettingsIcon } from "lucide-react";
import { useResearchProjects, useCreateResearch, useResearchProject } from "@/api/research";
import { useEffect } from "react";
import { wsManager } from "@/api/websocket";

type ResearchProjectSummary = {
    id: string;
    title: string;
    status: string;
    progress: number;
    time?: string;
};

type ResearchProjectDetail = {
    id?: string;
    title: string;
    status?: string;
    progress?: number;
    created_at?: string;
    query?: {
        main_query?: string;
    };
};

type ProjectProgressMessage = {
    id?: string;
    project_id?: string;
    progress_percentage?: number;
    status?: string;
};

function ResearchDetail({ id }: { id: string }) {
    const navigate = useNavigate();
    const { data: projectDataRaw, isLoading } = useResearchProject(id);
    const projectData = projectDataRaw as ResearchProjectDetail | undefined;
    const [progress, setProgress] = useState(0);
    const [liveStatus, setLiveStatus] = useState<string | null>(null);

    useEffect(() => {
        wsManager.connect();

        const unsubscribe = wsManager.subscribe('project_progress', (data: ProjectProgressMessage) => {
            if (data.project_id === id || data.id === id) {
                setProgress(data.progress_percentage || 0);
                setLiveStatus(data.status || 'running');
            }
        });

        wsManager.send({
            type: "subscription",
            data: { project_id: id }
        });

        return () => {
            unsubscribe();
            wsManager.disconnect();
        };
    }, [id]);

    if (isLoading) return <div className="p-8 text-center text-muted-foreground animate-pulse">Loading project details...</div>;

    if (!projectData) return <div className="p-8 text-center text-muted-foreground">Project not found</div>;

    const project = {
        id: projectData.id || id,
        title: projectData.title,
        status: liveStatus || projectData.status || 'pending',
        progress: progress,
        description: projectData.query?.main_query || "Research in progress...",
        createdAt: projectData.created_at || "Just now"
    };

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div className="flex items-center gap-4">
                <Button aria-label="Back to research projects" variant="ghost" size="icon" onClick={() => navigate("/app/research")}>
                    <ArrowLeft aria-hidden="true" className="h-5 w-5" />
                </Button>
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">{project.title}</h2>
                    <div className="flex items-center text-sm text-muted-foreground gap-3 mt-1">
                        <span className="font-mono bg-muted px-1.5 py-0.5 rounded text-xs">{project.id}</span>
                        <Badge variant="secondary" className="capitalize bg-primary/10 text-primary hover:bg-primary/20">{project.status}</Badge>
                    </div>
                </div>
            </div>

            <Tabs defaultValue="overview" className="w-full">
                <TabsList aria-label="Research project sections" className="mb-4">
                    <TabsTrigger value="overview">Overview</TabsTrigger>
                    <TabsTrigger value="agents">Agents</TabsTrigger>
                    <TabsTrigger value="results">Results</TabsTrigger>
                    <TabsTrigger value="settings">Settings</TabsTrigger>
                </TabsList>

                <TabsContent value="overview" className="space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Project Status</CardTitle>
                            <CardDescription>Current execution metrics and summary</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                <div>
                                    <div className="flex justify-between mb-1 text-sm font-medium">
                                        <span>Progress</span>
                                        <span>{project.progress}%</span>
                                    </div>
                                        <div
                                            aria-label="Project progress"
                                            aria-valuemax={100}
                                            aria-valuemin={0}
                                            aria-valuenow={project.progress}
                                            className="h-2 w-full bg-secondary rounded-full overflow-hidden"
                                            role="progressbar"
                                        >
                                            <div className="h-full bg-primary transition-all" style={{ width: `${project.progress}%` }} />
                                        </div>
                                </div>
                                <div className="text-sm mt-4">
                                    <p className="text-muted-foreground">{project.description}</p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="agents">
                    <Card>
                        <CardHeader>
                            <CardTitle>Assigned Agents</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="flex items-center gap-4 p-4 border rounded-lg bg-card text-card-foreground shadow-sm">
                                <Terminal className="h-8 w-8 text-primary" />
                                <div>
                                    <h4 className="font-semibold">Researcher Alpha</h4>
                                    <p className="text-xs text-muted-foreground">Gathering Q1 reports...</p>
                                </div>
                                <div className="ml-auto">
                                    <Badge>Active</Badge>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="results">
                    <Card>
                        <CardHeader>
                            <CardTitle>Findings & Citations</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="flex items-center gap-3 text-muted-foreground text-sm p-4 border rounded-lg border-dashed">
                                <FileText className="h-5 w-5" />
                                No results compiled yet. Wait for agent completion.
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="settings">
                    <Card>
                        <CardHeader>
                            <CardTitle>Project Settings</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="flex items-center gap-3 text-muted-foreground text-sm p-4 border rounded-lg border-dashed">
                                <SettingsIcon className="h-5 w-5" />
                                Settings panel placeholder.
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}

export function Research() {
    const { id } = useParams();
    const navigate = useNavigate();
    const { data: projects, isLoading } = useResearchProjects();
    const projectList = (projects ?? []) as ResearchProjectSummary[];
    const [searchQuery, setSearchQuery] = useState("");
    const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
    const createResearchList = useCreateResearch();

    const [newProject, setNewProject] = useState({ title: "", topic: "", description: "" });

    if (id) {
        return <ResearchDetail id={id} />;
    }

    const filteredProjects = projectList.filter((p) =>
        p.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.id.toLowerCase().includes(searchQuery.toLowerCase())
    );

    const handleCreate = () => {
        createResearchList.mutate(newProject, {
            onSuccess: () => {
                setIsCreateModalOpen(false);
                setNewProject({ title: "", topic: "", description: "" });
            }
        });
    };

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Research Projects</h2>
                    <p className="text-muted-foreground">Manage and monitor AI research tasks.</p>
                </div>
                <Dialog open={isCreateModalOpen} onOpenChange={setIsCreateModalOpen}>
                    <DialogTrigger asChild>
                        <Button className="gap-2">
                            <Plus className="h-4 w-4" /> New Research
                        </Button>
                    </DialogTrigger>
                    <DialogContent>
                        <DialogHeader>
                            <DialogTitle>Create Research Project</DialogTitle>
                            <DialogDescription>
                                Initialize a new multi-agent research task.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="space-y-4 py-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium" htmlFor="research-title">Title</label>
                                <Input
                                    id="research-title"
                                    placeholder="e.g. Q2 Competitor Analysis"
                                    value={newProject.title}
                                    onChange={(e) => setNewProject({ ...newProject, title: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium" htmlFor="research-topic">Topic / Objective</label>
                                <Input
                                    id="research-topic"
                                    placeholder="What should the agents discover?"
                                    value={newProject.topic}
                                    onChange={(e) => setNewProject({ ...newProject, topic: e.target.value })}
                                />
                            </div>
                        </div>
                        <DialogFooter>
                            <Button variant="outline" onClick={() => setIsCreateModalOpen(false)}>Cancel</Button>
                            <Button onClick={handleCreate} disabled={!newProject.title || createResearchList.isPending}>
                                Start Research
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
            </div>

            <div className="flex items-center gap-2 max-w-sm">
                <Search aria-hidden="true" className="h-4 w-4 text-muted-foreground absolute ml-3" />
                <Input
                    aria-label="Search research projects"
                    placeholder="Search projects by name or ID..."
                    className="pl-9"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                />
            </div>

            {isLoading ? (
                <div className="text-center py-12 text-muted-foreground flex flex-col items-center">
                    <Beaker className="h-8 w-8 animate-pulse mb-4 text-primary" />
                    <p>Loading projects...</p>
                </div>
            ) : (
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {filteredProjects.map((project) => (
                        <Card
                            key={project.id}
                            aria-label={`Open research project ${project.title}`}
                            className="cursor-pointer hover:border-primary/50 transition-colors hover:shadow-md"
                            onClick={() => navigate(`/app/research/${project.id}`)}
                            onKeyDown={(event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                    event.preventDefault();
                                    navigate(`/app/research/${project.id}`);
                                }
                            }}
                            role="button"
                            tabIndex={0}
                        >
                            <CardHeader className="pb-3">
                                <div className="flex items-start justify-between">
                                    <div className="flex items-center gap-3">
                                        <div className="h-8 w-8 rounded-md bg-primary/10 flex items-center justify-center">
                                            <Beaker className="h-4 w-4 text-primary" />
                                        </div>
                                        <div>
                                            <CardTitle className="text-base">{project.title}</CardTitle>
                                            <div className="text-xs text-muted-foreground font-mono mt-0.5">{project.id}</div>
                                        </div>
                                    </div>
                                </div>
                            </CardHeader>
                            <CardContent>
                                <div className="space-y-4">
                                    <div className="flex items-center justify-between">
                                        <Badge variant={project.status === 'completed' ? 'default' : project.status === 'failed' ? 'destructive' : 'secondary'}
                                            className={`capitalize ${project.status === 'completed' ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-none' : project.status === 'running' ? 'bg-primary/10 text-primary border-none' : ''}`}>
                                            {project.status}
                                        </Badge>
                                        <div className="flex items-center text-xs text-muted-foreground gap-1">
                                            <Clock className="h-3 w-3" /> {project.time}
                                        </div>
                                    </div>
                                    <div>
                                        <div className="flex justify-between mb-1.5 text-xs font-medium">
                                            <span className="text-muted-foreground">Progress</span>
                                            <span>{project.progress}%</span>
                                        </div>
                                        <div
                                            aria-label={`${project.title} progress`}
                                            aria-valuemax={100}
                                            aria-valuemin={0}
                                            aria-valuenow={project.progress}
                                            className="h-1.5 w-full bg-secondary rounded-full overflow-hidden"
                                            role="progressbar"
                                        >
                                            <div
                                                className={`h-full transition-all duration-1000 ${project.status === 'completed' ? 'bg-emerald-500' : project.status === 'failed' ? 'bg-destructive' : 'bg-primary'}`}
                                                style={{ width: `${project.progress}%` }}
                                            />
                                        </div>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    ))}

                    {filteredProjects.length === 0 && (
                        <div className="col-span-full text-center py-12 border rounded-xl border-dashed bg-card/50">
                            <p className="text-muted-foreground">No research projects found.</p>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
