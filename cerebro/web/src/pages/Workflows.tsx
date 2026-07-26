import { CircleDashed, ScrollText } from "lucide-react";

export function Workflows() {
    return (
        <section aria-labelledby="workflow-catalog-heading" className="workbench-page">
            <div className="workbench-page-intro">
                <p className="workbench-kicker">Choose a research protocol</p>
                <h2 id="workflow-catalog-heading" className="font-editorial text-3xl tracking-[-0.025em] md:text-4xl">
                    Workflow catalog
                </h2>
                <p className="workbench-lede">
                    Review a protocol’s outcome, version, operating limits, and source requirements before
                    beginning a controlled research run.
                </p>
            </div>

            <div className="workbench-rule" />

            <div className="workbench-empty-state" role="status">
                <span className="workbench-empty-mark" aria-hidden="true">
                    <ScrollText className="h-5 w-5" />
                </span>
                <div>
                    <p className="workbench-kicker">Catalog status</p>
                    <h3 className="font-editorial mt-1 text-xl">No workflow catalog has been loaded</h3>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                        This route is ready for the catalog connection. Workflow availability, maturity, and
                        execution requirements will remain unreported until the service supplies them.
                    </p>
                </div>
                <span className="workbench-provenance workbench-provenance-unavailable">
                    <CircleDashed aria-hidden="true" className="h-3.5 w-3.5" />
                    Data unavailable
                </span>
            </div>
        </section>
    );
}
