import { ArrowRight, Github, Info } from 'lucide-react';
import { ThemeToggle } from '@/components/layout/ThemeToggle';

const GITHUB_URL = 'https://github.com/jsogarro/cerebro';
const DOCS_URL = 'https://github.com/jsogarro/cerebro/tree/main/docs';

const primitives = [
  { num: '01', name: 'Workflow', role: 'versioned procedure' },
  { num: '02', name: 'Run', role: 'bounded execution' },
  { num: '03', name: 'Task', role: 'unit of work' },
  { num: '04', name: 'Evidence', role: 'claim provenance' },
  { num: '05', name: 'Artifact', role: 'durable output' },
  { num: '06', name: 'Evaluator', role: 'verification result' },
];

// Each command maps 1:1 to a real `research-cli agents` subcommand and its
// verbatim Click docstring (src/cli/commands/agents.py). Every argument/flag
// shown is real, so each line is copy-paste runnable.
const cliLines = [
  { comment: 'Execute intelligent MASR-routed query.', sub: 'query', arg: null, str: '"Compare two approaches to retrieval evaluation"', flags: null },
  { comment: 'Get MASR routing decision with cost optimization.', sub: 'route', arg: null, str: '"Summarize the evidence for this claim"', flags: null },
  { comment: 'Estimate execution cost with detailed breakdown.', sub: 'estimate', arg: null, str: '"Produce a comparative research brief"', flags: null },
  { comment: 'Direct agent execution (bypass MASR routing).', sub: 'execute', arg: 'literature-review', str: '"Review the literature on retrieval evaluation"', flags: null },
  { comment: 'Execute Chain-of-Agents workflow.', sub: 'chain', arg: null, str: '"Draft, critique, and revise a summary"', flags: '-a researcher -a critic' },
  { comment: 'Get MASR router health and performance metrics.', sub: 'status', arg: null, str: null, flags: null },
];

const capabilities = [
  {
    idx: '01',
    name: 'Workflow',
    kind: 'versioned definition',
    body: 'A declarative, versioned description of a research procedure. A run always targets a specific workflow version, so the same definition can be re-run and compared over time.',
  },
  {
    idx: '02',
    name: 'Run',
    kind: 'bounded execution',
    body: 'A single execution of a workflow, bounded by a budget and inspectable from start to finish — its status, timings, and the decisions taken along the way are recorded, not summarized away.',
  },
  {
    idx: '03',
    name: 'Task',
    kind: 'unit of work',
    body: 'The ordered steps that make up a run. Each task and event is logged so the sequence of work can be read back and audited rather than inferred after the fact.',
  },
  {
    idx: '04',
    name: 'Evidence',
    kind: 'claim provenance',
    body: 'The source material a claim rests on: its identity, when it was retrieved, the excerpt used, and its verifier status. Every claim links to the evidence behind it, or is marked as unsupported.',
  },
  {
    idx: '05',
    name: 'Artifact',
    kind: 'durable output',
    body: 'The durable output a run produces — the research artifact together with its claim-and-evidence ledger — retained after the run ends so it can be reopened and inspected later.',
  },
  {
    idx: '06',
    name: 'Evaluator',
    kind: 'verification result',
    body: "Checks that grade a run's output. Each evaluator reports a status, a severity, and its own metrics — surfacing where a result is weak instead of collapsing everything to one pass/fail.",
  },
];

const priorities = [
  { lead: 'Versioned workflows', rest: 'every run is pinned to an exact workflow version, so results stay comparable across changes.' },
  { lead: 'Inspectable runs and tasks', rest: 'the full ordered sequence of steps and events is recorded and replayable.' },
  { lead: 'Source evidence and claim provenance', rest: 'each claim traces to its supporting sources, or is labeled unsupported.' },
  { lead: 'Durable artifacts', rest: 'outputs and their evidence ledgers persist beyond the run that produced them.' },
  { lead: 'Verification and evaluation results', rest: 'evaluator status, severity, and metrics travel with the artifact.' },
  { lead: 'Cost, latency, and execution traces', rest: 'reported when measured, and marked unavailable when they are not.' },
];

function BrandMark() {
  return (
    <svg
      className="lp-brand-mark"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="6" cy="7" r="2" />
      <circle cx="6" cy="17" r="2" />
      <circle cx="18" cy="12" r="2" />
      <path d="M8 7h4a2 2 0 0 1 2 2v1M8 17h4a2 2 0 0 0 2-2v-1" />
      <path d="M16 12h.01" />
    </svg>
  );
}

export function Landing() {
  return (
    <div className="lp-root">
      <div className="lp-nav-wrap">
        <nav className="lp-nav" aria-label="Primary">
          <a className="lp-brand" href="/" aria-label="Cerebro home">
            <BrandMark />
            Cerebro
          </a>
          <div className="lp-nav-right">
            <ul className="lp-nav-links">
              <li>
                <a href={DOCS_URL} target="_blank" rel="noreferrer">
                  Documentation
                </a>
              </li>
              <li>
                <a href={GITHUB_URL} target="_blank" rel="noreferrer">
                  GitHub
                </a>
              </li>
              <li>
                <a href="#cli">API reference</a>
              </li>
            </ul>
            <ThemeToggle />
          </div>
        </nav>
      </div>

      <main className="lp-shell">
        {/* Hero */}
        <section className="lp-hero" aria-labelledby="lp-hero-title">
          <div className="lp-hero-lede">
            <p className="lp-eyebrow">Open-source research workbench</p>
            <h1 id="lp-hero-title">
              An open-source workbench for building, running, and evaluating{' '}
              <span className="lp-accent">source-grounded</span> AI research workflows.
            </h1>
            <div className="lp-accent-rule" aria-hidden="true" />
            <p className="lp-lede">
              Cerebro treats a workflow, its runs, and every claim's supporting evidence as
              inspectable, versioned objects — so a result can be traced back to what produced it,
              not just read at face value.
            </p>
            <div className="lp-hero-cta">
              <a
                className="lp-btn lp-btn--primary"
                href={GITHUB_URL}
                target="_blank"
                rel="noreferrer"
              >
                View on GitHub
                <ArrowRight aria-hidden="true" />
              </a>
              <a className="lp-btn lp-btn--secondary" href={DOCS_URL} target="_blank" rel="noreferrer">
                Read the docs
              </a>
            </div>
            <p className="lp-hero-note">
              <Info aria-hidden="true" />
              Cerebro reports what it can measure and marks what it can't. It does not fabricate
              results, metrics, or sources.
            </p>
          </div>

          <aside className="lp-primitives-card" aria-label="Core primitives">
            <div className="lp-pc-head">
              <p className="lp-eyebrow">Core primitives</p>
              <span className="lp-pc-count">6</span>
            </div>
            <ul className="lp-primitives-index">
              {primitives.map((primitive) => (
                <li key={primitive.num}>
                  <span className="lp-pi-num">{primitive.num}</span>
                  <span className="lp-pi-name">{primitive.name}</span>
                  <span className="lp-pi-role">{primitive.role}</span>
                </li>
              ))}
            </ul>
          </aside>
        </section>

        {/* CLI reference */}
        <section className="lp-section" id="cli" aria-labelledby="lp-cli-title">
          <div className="lp-section-head">
            <p className="lp-eyebrow">Command-line interface</p>
            <h2 id="lp-cli-title">
              Drive the workbench from <code>research-cli</code>
            </h2>
            <p>
              The documented agent commands, exactly as they appear in the CLI reference. Each one
              names what it does — no scripted output is shown here.
            </p>
          </div>
          <div className="lp-cli">
            <div className="lp-cli-bar">
              <span className="lp-cli-dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              <span className="lp-cli-title">research-cli — agents</span>
            </div>
            <pre className="lp-cli-body">
              <code>
                {cliLines.map((line) => (
                  <span key={line.sub}>
                    <span className="lp-cli-line lp-cli-cmt"># {line.comment}</span>
                    <span className="lp-cli-line lp-cli-cmd">
                      <span className="lp-cli-prompt">$ </span>
                      <span className="lp-cli-bin">research-cli</span> agents{' '}
                      <span className="lp-cli-sub">{line.sub}</span>
                      {line.arg ? ` ${line.arg}` : ''}
                      {line.str ? (
                        <>
                          {' '}
                          <span className="lp-cli-str">{line.str}</span>
                        </>
                      ) : null}
                      {line.flags ? ` ${line.flags}` : ''}
                    </span>
                  </span>
                ))}
              </code>
            </pre>
          </div>
        </section>

        {/* Capabilities / primitives */}
        <section className="lp-section" id="capabilities" aria-labelledby="lp-cap-title">
          <div className="lp-section-head">
            <p className="lp-eyebrow">What the workbench models</p>
            <h2 id="lp-cap-title">Six primitives, each an inspectable object</h2>
            <p>
              Cerebro's vocabulary is small and load-bearing. Everything you build, run, and check
              is expressed in these terms — and each carries its own state, history, and provenance.
            </p>
          </div>
          <div className="lp-cap-grid">
            {capabilities.map((cap) => (
              <article className="lp-cap" key={cap.idx}>
                <div className="lp-cap-top">
                  <span className="lp-cap-idx">{cap.idx}</span>
                  <h3 className="lp-cap-name">{cap.name}</h3>
                </div>
                <p className="lp-cap-kind">{cap.kind}</p>
                <p>{cap.body}</p>
              </article>
            ))}
          </div>
        </section>

        {/* Priorities */}
        <section className="lp-section" aria-labelledby="lp-pri-title">
          <div className="lp-section-head">
            <p className="lp-eyebrow">What it makes first-class</p>
            <h2 id="lp-pri-title">Designed around traceability</h2>
            <p>
              The properties Cerebro treats as primary — the things it will always show you rather
              than hide.
            </p>
          </div>
          <div className="lp-priorities">
            <ul>
              {priorities.map((item) => (
                <li key={item.lead}>
                  <span className="lp-marker" aria-hidden="true">
                    ●
                  </span>
                  <span className="lp-p-text">
                    <strong>{item.lead}</strong> — {item.rest}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Closing */}
        <section className="lp-closing" aria-labelledby="lp-os-title">
          <div className="lp-closing-text">
            <p className="lp-eyebrow">Open source</p>
            <h2 id="lp-os-title">Read the source, then decide</h2>
            <p>
              Cerebro is open source. The workflows, run records, and evaluation logic described
              above are all in the repository — inspect them before you rely on them.
            </p>
          </div>
          <a className="lp-btn lp-btn--primary" href={GITHUB_URL} target="_blank" rel="noreferrer">
            View on GitHub
            <Github aria-hidden="true" />
          </a>
        </section>
      </main>

      <footer className="lp-foot">
        <span>cerebro workbench · open-source</span>
        <nav aria-label="Footer">
          <a href={DOCS_URL} target="_blank" rel="noreferrer">
            Documentation
          </a>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer">
            GitHub
          </a>
          <a href="#cli">API reference</a>
        </nav>
      </footer>
    </div>
  );
}
