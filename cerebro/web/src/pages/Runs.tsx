import { CircleDashed, FileClock } from "lucide-react";

export function Runs() {
    return (
        <section aria-labelledby="run-ledger-heading" className="workbench-page">
            <div className="workbench-page-intro">
                <p className="workbench-kicker">Observe and reopen research</p>
                <h2 id="run-ledger-heading" className="font-editorial text-3xl tracking-[-0.025em] md:text-4xl">
                    Run ledger
                </h2>
                <p className="workbench-lede">
                    Scan execution state and reopen prior work without losing its workflow version, source
                    provenance, evaluation, or operating context.
                </p>
            </div>

            <div className="workbench-rule" />

            <div className="workbench-empty-state" role="status">
                <span className="workbench-empty-mark" aria-hidden="true">
                    <FileClock className="h-5 w-5" />
                </span>
                <div>
                    <p className="workbench-kicker">Ledger status</p>
                    <h3 className="font-editorial mt-1 text-xl">No run history has been loaded</h3>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                        This route is ready for the run service. States, timings, evaluations, and costs will
                        remain unreported until a response establishes their values and origins.
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
