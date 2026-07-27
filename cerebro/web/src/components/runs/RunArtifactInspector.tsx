import { useEffect, useRef, useState } from 'react';
import { ArrowUp, BookOpen, Quote } from 'lucide-react';
import {
  availabilitySemantics,
  supportSemantics,
  type Artifact,
  type ClaimSupport,
  type Evidence,
  type PresentationState,
} from '@/api/workbench';
import { Button } from '@/components/ui/button';
import { StatusBadge } from './StatusBadge';
import { formatTimestamp } from './format';

interface RunArtifactInspectorProps {
  artifact: Artifact | null;
  evidence: readonly Evidence[];
  evidenceState: PresentationState;
  fixtureMode: boolean;
}

function textValue(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return null;
}

function ArtifactContent({ content }: { content: unknown }) {
  if (content === null || content === undefined) {
    return <p className="text-sm text-muted-foreground">Artifact prose is not available.</p>;
  }
  if (typeof content === 'string') {
    return <p className="whitespace-pre-wrap text-[1.02rem] leading-8">{content}</p>;
  }
  if (typeof content !== 'object' || Array.isArray(content)) {
    return <p className="text-sm text-muted-foreground">The artifact content is not structured for reading.</p>;
  }

  const record = content as Record<string, unknown>;
  const title = textValue(record.title);
  const summary = textValue(record.summary) ?? textValue(record.abstract);
  const sections = Array.isArray(record.sections) ? record.sections : [];
  const reserved = new Set(['title', 'summary', 'abstract', 'sections']);
  const proseFields = Object.entries(record).filter(
    ([key, value]) => !reserved.has(key) && textValue(value) !== null,
  );

  return (
    <div className="run-artifact-prose">
      {title ? <h5>{title}</h5> : null}
      {summary ? <p className="run-artifact-deck">{summary}</p> : null}
      {sections.map((section, index) => {
        if (typeof section !== 'object' || section === null || Array.isArray(section)) return null;
        const sectionRecord = section as Record<string, unknown>;
        const heading = textValue(sectionRecord.heading) ?? textValue(sectionRecord.title);
        const body =
          textValue(sectionRecord.body) ??
          textValue(sectionRecord.prose) ??
          textValue(sectionRecord.content);
        if (!heading && !body) return null;
        return (
          <section key={`${heading ?? 'section'}-${index}`}>
            {heading ? <h6>{heading}</h6> : null}
            {body ? <p className="whitespace-pre-wrap">{body}</p> : null}
          </section>
        );
      })}
      {proseFields.map(([key, value]) => (
        <section key={key}>
          <h6>{key.replaceAll('_', ' ')}</h6>
          <p className="whitespace-pre-wrap">{textValue(value)}</p>
        </section>
      ))}
    </div>
  );
}

function EvidenceRecord({
  item,
  selected,
  activeClaim,
  setExcerptRef,
  onReturn,
}: {
  item: Evidence;
  selected: boolean;
  activeClaim: ClaimSupport | null;
  setExcerptRef: (node: HTMLQuoteElement | null) => void;
  onReturn: () => void;
}) {
  const sourceName = item.title ?? item.publisher ?? item.locator;
  return (
    <article
      className={`run-evidence-record ${selected ? 'run-evidence-record-selected' : ''}`}
      aria-labelledby={`evidence-title-${item.id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="workbench-kicker">{item.sourceType}</p>
          <h5 id={`evidence-title-${item.id}`} className="mt-1 break-words font-editorial text-lg">
            {sourceName}
          </h5>
        </div>
        <StatusBadge
          semantics={availabilitySemantics[item.availability]}
          rawValue={item.rawAvailability}
        />
      </div>
      <dl className="run-evidence-meta">
        <div>
          <dt>Locator</dt>
          <dd>{item.locator}</dd>
        </div>
        <div>
          <dt>Publisher / source identity</dt>
          <dd>{item.publisher ?? 'Not recorded'}</dd>
        </div>
        <div>
          <dt>Content hash</dt>
          <dd className="font-mono">{item.contentHash ?? 'Not recorded'}</dd>
        </div>
        <div>
          <dt>Source version</dt>
          <dd>{item.sourceVersion ?? 'Not recorded'}</dd>
        </div>
        <div>
          <dt>Retrieved</dt>
          <dd>{formatTimestamp(item.retrievedAt)}</dd>
        </div>
        <div>
          <dt>Fixture reference</dt>
          <dd>{item.fixtureReference ?? 'Not applicable or not recorded'}</dd>
        </div>
      </dl>
      {item.excerpt ? (
        <blockquote
          ref={setExcerptRef}
          id={`evidence-excerpt-${item.id}`}
          tabIndex={-1}
          className="run-evidence-excerpt"
          aria-label={`Exact evidence excerpt from ${sourceName}`}
        >
          <Quote aria-hidden="true" className="h-4 w-4 flex-shrink-0" />
          <span>{item.excerpt}</span>
        </blockquote>
      ) : (
        <p className="mt-4 border-l-2 border-border pl-3 text-sm leading-6 text-muted-foreground">
          No exact excerpt is available. Availability is {availabilitySemantics[item.availability].label.toLowerCase()}.
        </p>
      )}
      {selected && activeClaim ? (
        <div className="mt-4 border-t border-border/70 pt-4">
          <p className="workbench-kicker">Why this evidence is linked</p>
          <p className="mt-1 text-sm leading-6">{activeClaim.explanation}</p>
          <Button variant="ghost" size="sm" className="mt-2 -ml-2 gap-2" onClick={onReturn}>
            <ArrowUp aria-hidden="true" className="h-4 w-4" />
            Return to claim
          </Button>
        </div>
      ) : null}
      <details className="run-developer-detail">
        <summary>Source retrieval detail</summary>
        <dl>
          <div>
            <dt>Retrieval tool</dt>
            <dd>{item.retrievalTool ?? 'Not recorded'}</dd>
          </div>
          <div>
            <dt>Usage classification</dt>
            <dd>{item.usageClassification ?? 'Not recorded'}</dd>
          </div>
          <div>
            <dt>Source timestamp</dt>
            <dd>{formatTimestamp(item.sourceTimestamp)}</dd>
          </div>
          <div>
            <dt>Retrieval parameters</dt>
            <dd className="font-mono">
              {item.retrievalParameters ? JSON.stringify(item.retrievalParameters) : 'Not recorded'}
            </dd>
          </div>
        </dl>
      </details>
    </article>
  );
}

export function RunArtifactInspector({
  artifact,
  evidence,
  evidenceState,
  fixtureMode,
}: RunArtifactInspectorProps) {
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const [activeClaim, setActiveClaim] = useState<ClaimSupport | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const evidenceRefs = useRef(new Map<string, HTMLQuoteElement>());
  const claimRefs = useRef(new Map<string, HTMLButtonElement>());
  const focusFrameRef = useRef<number | null>(null);
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));
  const evidenceIsResolved =
    evidenceState === 'ready' || evidenceState === 'partial' || evidenceState === 'empty';

  useEffect(() => {
    return () => {
      if (focusFrameRef.current !== null) cancelAnimationFrame(focusFrameRef.current);
    };
  }, []);

  const focusEvidence = (
    claim: ClaimSupport,
    evidenceId: string,
    control: HTMLButtonElement,
  ) => {
    const item = evidenceById.get(evidenceId);
    const excerpt = evidenceRefs.current.get(evidenceId);
    if (!item || !excerpt) return;
    claimRefs.current.set(claim.claimId, control);
    setActiveClaim(claim);
    setSelectedEvidenceId(evidenceId);
    setAnnouncement('');
    if (focusFrameRef.current !== null) cancelAnimationFrame(focusFrameRef.current);
    focusFrameRef.current = requestAnimationFrame(() => {
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      excerpt.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' });
      excerpt.focus({ preventScroll: true });
      setAnnouncement(`Focused exact evidence excerpt from ${item.title ?? item.locator} for claim: ${claim.claimText}`);
      focusFrameRef.current = null;
    });
  };

  const returnToClaim = () => {
    if (!activeClaim) return;
    const control = claimRefs.current.get(activeClaim.claimId);
    control?.focus();
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    control?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' });
    setAnnouncement(`Returned to claim: ${activeClaim.claimText}`);
  };

  return (
    <div className={`run-inspection-layout ${artifact === null ? 'run-inspection-layout-evidence-only' : ''}`}>
      {artifact ? (
      <article className="run-artifact-sheet" aria-labelledby={`artifact-${artifact.id}-heading`}>
        <div className="run-artifact-masthead">
          <div>
            <p className="workbench-kicker">Research artifact</p>
            <h4 id={`artifact-${artifact.id}-heading`} className="mt-1 font-editorial text-2xl">
              {artifact.type}
            </h4>
          </div>
          {fixtureMode ? (
            <span className="workbench-provenance workbench-provenance-fixture">
              Controlled fixture context
            </span>
          ) : null}
        </div>
        <dl className="run-artifact-folio">
          <div>
            <dt>Schema</dt>
            <dd>{artifact.schemaVersion}</dd>
          </div>
          <div>
            <dt>Workflow</dt>
            <dd>{artifact.workflowId} · {artifact.workflowVersion}</dd>
          </div>
          <div>
            <dt>Content hash</dt>
            <dd className="font-mono">{artifact.contentHash ?? 'Not recorded'}</dd>
          </div>
        </dl>
        <ArtifactContent content={artifact.content} />

        <section className="run-claims" aria-labelledby={`claims-${artifact.id}-heading`}>
          <p className="workbench-kicker">Material claim register</p>
          <h4 id={`claims-${artifact.id}-heading`} className="mt-1 font-editorial text-xl">
            Claims and support
          </h4>
          {artifact.claims.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">
              No material claim records were attached to this artifact.
            </p>
          ) : (
            <ol className="mt-4 space-y-5">
              {artifact.claims.map((claim, index) => {
                const exactEvidence = evidenceIsResolved
                  ? claim.evidenceIds.filter((evidenceId) => evidenceById.get(evidenceId)?.excerpt)
                  : [];
                const missingEvidence = evidenceIsResolved
                  ? claim.evidenceIds.filter((evidenceId) => !evidenceById.has(evidenceId))
                  : [];
                const evidenceWithoutExcerpt = evidenceIsResolved
                  ? claim.evidenceIds.filter((evidenceId) => {
                      const item = evidenceById.get(evidenceId);
                      return item !== undefined && item.excerpt === null;
                    })
                  : [];
                const unusableEvidence = evidenceIsResolved
                  ? claim.evidenceIds.filter((evidenceId) => {
                      const item = evidenceById.get(evidenceId);
                      return (
                        item !== undefined &&
                        item.availability !== 'available' &&
                        item.availability !== 'partial'
                      );
                    })
                  : [];
                const hasUsableEvidence = evidenceIsResolved
                  ? claim.evidenceIds.some((evidenceId) => {
                      const item = evidenceById.get(evidenceId);
                      return (
                        item !== undefined &&
                        (item.availability === 'available' ||
                          item.availability === 'partial') &&
                        item.excerpt !== null
                      );
                    })
                  : false;
                const supportCannotBeVerified =
                  evidenceIsResolved &&
                  ((claim.status === 'supported' &&
                    (missingEvidence.length > 0 ||
                      unusableEvidence.length > 0 ||
                      evidenceWithoutExcerpt.length > 0)) ||
                    (claim.status === 'partially-supported' && !hasUsableEvidence));
                return (
                  <li key={claim.claimId} className="run-claim">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <p className="max-w-2xl font-editorial text-lg leading-7">
                        <span className="mr-2 font-mono text-xs text-muted-foreground">
                          {String(index + 1).padStart(2, '0')}
                        </span>
                        {claim.claimText}
                      </p>
                      <StatusBadge
                        semantics={
                          supportSemantics[supportCannotBeVerified ? 'unknown' : claim.status]
                        }
                        rawValue={
                          supportCannotBeVerified
                            ? (claim.rawStatus ?? claim.status)
                            : claim.rawStatus
                        }
                      />
                    </div>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      {claim.explanation}
                    </p>
                    {!evidenceIsResolved ? (
                      <p className="mt-3 text-sm font-medium">
                        Evidence inspection is {evidenceState}; citation targets cannot be resolved yet.
                      </p>
                    ) : exactEvidence.length > 0 ? (
                      <div className="mt-3 flex flex-wrap gap-2" aria-label="Citation controls">
                        {exactEvidence.map((evidenceId, evidenceIndex) => (
                          <Button
                            key={evidenceId}
                            variant="outline"
                            size="sm"
                            className="gap-2"
                            onClick={(event) => focusEvidence(claim, evidenceId, event.currentTarget)}
                          >
                            <BookOpen aria-hidden="true" className="h-4 w-4" />
                            Inspect exact evidence {evidenceIndex + 1}
                          </Button>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-3 text-sm font-medium">
                        {claim.evidenceIds.length === 0
                          ? 'No evidence is linked; there is no citation target to inspect.'
                          : 'Linked evidence has no exact excerpt available, so no citation control is provided.'}
                      </p>
                    )}
                    {missingEvidence.length > 0 ? (
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">
                        {missingEvidence.length} linked evidence record{missingEvidence.length === 1 ? '' : 's'} {missingEvidence.length === 1 ? 'is' : 'are'} missing, so the reported support cannot be independently verified.
                      </p>
                    ) : null}
                    {unusableEvidence.length > 0 ? (
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">
                        {unusableEvidence.length} linked evidence record{unusableEvidence.length === 1 ? '' : 's'} {unusableEvidence.length === 1 ? 'is' : 'are'} not available for independent verification.
                      </p>
                    ) : null}
                    {evidenceWithoutExcerpt.length > 0 ? (
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">
                        {evidenceWithoutExcerpt.length} linked evidence record{evidenceWithoutExcerpt.length === 1 ? '' : 's'} cannot provide an exact excerpt.
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          )}
        </section>
        <details className="run-developer-detail">
          <summary>Artifact generation detail</summary>
          <dl>
            <div><dt>Artifact ID</dt><dd className="font-mono">{artifact.id}</dd></div>
            <div><dt>Generating task</dt><dd>{artifact.generatingTaskId ?? 'Not recorded'}</dd></div>
            <div><dt>Generating phase</dt><dd>{artifact.generatingPhase ?? 'Not recorded'}</dd></div>
            <div><dt>Created</dt><dd>{formatTimestamp(artifact.createdAt)}</dd></div>
            <div><dt>Validation</dt><dd>{artifact.validation}</dd></div>
          </dl>
        </details>
      </article>
      ) : null}

      <aside className="run-evidence-rail" aria-labelledby="evidence-inspector-heading">
        <p className="workbench-kicker">Provenance rail</p>
        <h4 id="evidence-inspector-heading" className="mt-1 font-editorial text-xl">
          Exact evidence
        </h4>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Source identity, locator, excerpt, and retrieval provenance remain independently inspectable.
        </p>
        <div className="mt-5 space-y-5">
          {!evidenceIsResolved ? (
            <p className="border-y border-border py-5 text-sm leading-6 text-muted-foreground">
              Evidence inspection is {evidenceState}. No conclusion about record or excerpt absence has been made.
            </p>
          ) : evidence.length > 0 ? (
            evidence.map((item) => (
              <EvidenceRecord
                key={item.id}
                item={item}
                selected={selectedEvidenceId === item.id}
                activeClaim={selectedEvidenceId === item.id ? activeClaim : null}
                setExcerptRef={(node) => {
                  if (node) evidenceRefs.current.set(item.id, node);
                  else evidenceRefs.current.delete(item.id);
                }}
                onReturn={returnToClaim}
              />
            ))
          ) : (
            <p className="border-y border-border py-5 text-sm leading-6 text-muted-foreground">
              No evidence records were returned. Claim explanations remain available in the artifact.
            </p>
          )}
        </div>
      </aside>
      <p className="sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>
    </div>
  );
}
