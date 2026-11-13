/** Submit Advisor: of every Alpha the Tasks produced, which are worth submitting, in what order.
 *
 * Nothing here submits. Submission happens on BRAIN, and it is permanent, so the advice is read
 * strictly: only an Alpha whose checks BRAIN has finished and passed is recommended, and one
 * that repeats a better pick (or something already submitted) is set aside rather than listed
 * twice.
 */

import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { CopyIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { fmt, isNum } from '@/lib/format'
import { useRefetchOn } from '@/lib/ws'
import { OpenInBrain, RecheckButton } from '@/screens/pool/shared'
import {
  Badge,
  Button,
  Chips,
  Disclosure,
  Empty,
  ErrorNotice,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Skeleton,
  type Tone,
} from '@/ui/kit'
import { ADVICE_KEY, type AdvisedAlpha, submitAdvisor } from './api'

const scoreTone = (score: number): Tone =>
  score >= 70 ? 'profit' : score >= 50 ? 'neutral' : 'warn'

const settingsLine = (a: AdvisedAlpha) => {
  const s = a.settings ?? {}
  return [
    s['region'],
    s['universe'],
    isNum(s['delay']) ? `D${s['delay']}` : null,
    s['neutralization'],
    isNum(s['decay']) ? `Decay ${s['decay']}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
}

export function SubmitAdvisorScreen() {
  const query = useQuery({ queryKey: ADVICE_KEY, queryFn: submitAdvisor.advice })
  // Reading every task's Alphas takes seconds on a large account, so not on every update.
  useRefetchOn('studies', ADVICE_KEY, 30_000)
  const [picked, setPicked] = useState<ReadonlySet<number>>(new Set())

  const tasks = useMemo(() => {
    const seen = new Map<number, string>()
    for (const a of query.data?.alphas ?? []) seen.set(a.taskId, a.taskName)
    return [...seen].sort((x, y) => x[0] - y[0])
  }, [query.data])

  const shown = useMemo(
    () => (query.data?.alphas ?? []).filter((a) => picked.size === 0 || picked.has(a.taskId)),
    [query.data, picked],
  )
  const by = (status: AdvisedAlpha['status']) => shown.filter((a) => a.status === status)
  const recommended = by('recommended')
  const needsCheck = by('needs_check')
  const setAside = shown.filter((a) => a.status === 'similar' || a.status === 'held_back')
  const waiting = by('waiting')
  const data = query.data

  return (
    <Page>
      <PageHeader
        title="Submit Advisor"
        description="Which Alphas from your Tasks are worth submitting, best first, with the reason for each."
        actions={
          <Button size="sm" variant="secondary" onClick={() => query.refetch()}>
            Refresh
          </Button>
        }
      />

      {query.isError && (
        <ErrorNotice error={query.error} title="Could not read the Tasks' Alphas" />
      )}

      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <Metric
            boxed
            label="Recommended"
            value={fmt.int(data.recommended)}
            tone={data.recommended > 0 ? 'profit' : 'neutral'}
          />
          <Metric
            boxed
            label="Need a BRAIN check"
            value={fmt.int(data.needsCheck)}
            tone={data.needsCheck > 0 ? 'warn' : 'neutral'}
          />
          <Metric boxed label="Repeat another" value={fmt.int(data.similar)} />
          <Metric boxed label="Held back" value={fmt.int(data.heldBack)} />
          <Metric boxed label="Already submitted" value={fmt.int(data.submitted)} />
        </div>
      )}

      {tasks.length > 1 && (
        <Panel title="Tasks" description="Every task counts, unless you narrow it.">
          <Disclosure
            summary={
              picked.size === 0
                ? `All ${tasks.length} tasks`
                : `${picked.size} of ${tasks.length} tasks`
            }
          >
            <Chips
              label="Tasks to advise over"
              items={tasks.map(([id, name]) => ({
                value: String(id),
                label: `Task ${id} · ${name}`,
              }))}
              value={[...picked].map(String)}
              onChange={(next) => setPicked(new Set(next.map(Number)))}
            />
          </Disclosure>
        </Panel>
      )}

      {query.isPending ? (
        <Skeleton className="h-96" label="Reading the Tasks' Alphas" />
      ) : data && data.considered === 0 ? (
        <Empty title="No submittable Alphas in your Tasks yet">
          Run a task. Alphas BRAIN has not refused appear here once their checks resolve.
        </Empty>
      ) : (
        <>
          <Panel
            title="Submit these, in this order"
            description="Ranked by score, and each one distinct from every better pick and from everything already submitted."
            bodyClassName="flex flex-col gap-3"
          >
            {recommended.length === 0 ? (
              <Empty title="Nothing is safe to submit yet">
                {needsCheck.length > 0
                  ? `${needsCheck.length} look promising but BRAIN has not finished checking them. Re-check them below.`
                  : 'No Alpha has cleared every check and stayed distinct.'}
              </Empty>
            ) : (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {recommended.map((a) => (
                  <AlphaCard key={a.alphaId} alpha={a} />
                ))}
              </div>
            )}
          </Panel>

          {needsCheck.length > 0 && (
            <Panel
              title={`Need a BRAIN check first (${needsCheck.length})`}
              description="Nothing has refused these, but BRAIN has not agreed either, and a submission cannot be undone. Re-check, then they move up if they pass. BRAIN limits these checks per hour."
            >
              <Disclosure summary={`Show ${needsCheck.length}`}>
                <div className="grid grid-cols-1 gap-3 p-3 xl:grid-cols-2">
                  {needsCheck.map((a) => (
                    <AlphaCard key={a.alphaId} alpha={a} recheck />
                  ))}
                </div>
              </Disclosure>
            </Panel>
          )}

          {setAside.length > 0 && (
            <Panel
              title={`Set aside (${setAside.length})`}
              description="Passed BRAIN's checks but repeat a better pick, miss a bar, or fall beyond the top ten."
            >
              <Disclosure summary={`Show ${setAside.length}`}>
                <div className="grid grid-cols-1 gap-3 p-3 xl:grid-cols-2">
                  {setAside.map((a) => (
                    <AlphaCard key={a.alphaId} alpha={a} />
                  ))}
                </div>
              </Disclosure>
            </Panel>
          )}

          {waiting.length > 0 && (
            <Notice tone="warn" title={`${waiting.length} cannot be judged yet`}>
              These were simulated in Quick mode, or a check came back ERROR. They return once BRAIN
              can judge them.
            </Notice>
          )}
        </>
      )}

      <Panel title="How the score works">
        <Disclosure summary="Out of 100, from figures already shown on each card">
          <ul className="flex list-disc flex-col gap-1 p-3 pl-7 text-body-compact text-ink-subtle">
            <li>
              45: Sharpe headroom over the bar (1.58 at Delay 1, 2.69 at Delay 0), judged on the
              weakest of Sharpe, after-cost Sharpe and test-year Sharpe.
            </li>
            <li>20: Fitness headroom over its bar (1.0, or 1.5 at Delay 0).</li>
            <li>
              15: how much of the Sharpe survives the held-out test years. Zero without a test
              period.
            </li>
            <li>10: turnover, full marks at 30% or less, none at 70%.</li>
            <li>10: Production Correlation, lower is better; unchecked scores halfway.</li>
          </ul>
          <p className="p-3 pt-0 text-body-compact text-ink-subtle">
            The score only ranks. It never overrides a check: a missed bar, a repeat of a better
            Alpha (PnL correlation of 0.7 or more over the last four calendar years), or a check
            BRAIN has not finished keeps an Alpha out of the recommendations.
          </p>
        </Disclosure>
      </Panel>
    </Page>
  )
}

function AlphaCard({ alpha: a, recheck }: { alpha: AdvisedAlpha; recheck?: boolean }) {
  const navigate = useNavigate()
  const copy = () =>
    navigator.clipboard.writeText(a.expression ?? '').then(
      () => toast.success(`Copied the expression of ${a.alphaId}`),
      () => toast.error('Could not write to the clipboard.'),
    )
  return (
    <article className="panel-highlight flex min-w-0 flex-col gap-3 rounded-lg border border-hairline bg-surface-1 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hairline-subtle pb-2">
        <div className="flex items-center gap-2">
          {a.rank > 0 && <Badge tone="profit">#{a.rank}</Badge>}
          <button
            type="button"
            className="num font-semibold text-body text-ink transition-colors hover:text-link hover:underline"
            onClick={() => void navigate({ to: '/alpha/$alphaId', params: { alphaId: a.alphaId } })}
          >
            {a.alphaId}
          </button>
        </div>
        <Badge tone={scoreTone(a.score)} title="Score out of 100">
          Score {fmt.ratio(a.score, 0)}
        </Badge>
      </div>
      <p className="num text-body-compact text-ink-subtle">
        {settingsLine(a)} · Task {a.taskId} · {a.taskName}
      </p>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        <Metric boxed size="sm" label="Sharpe" value={fmt.ratio(a.sharpe)} />
        <Metric boxed size="sm" label="Fitness" value={fmt.ratio(a.fitness)} />
        <Metric boxed size="sm" label="Turnover" value={fmt.pct(a.turnover)} />
        <Metric boxed size="sm" label="After cost" value={fmt.ratio(a.afterCostSharpe)} />
        <Metric boxed size="sm" label="Test Sharpe" value={fmt.ratio(a.testSharpe)} />
        <Metric boxed size="sm" label="Prod corr" value={fmt.ratio(a.prodCorrelation)} />
      </div>
      {a.strengths.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-body-compact text-pnl-positive-text">
          {a.strengths.map((s) => (
            <li key={s}>+ {s}</li>
          ))}
        </ul>
      )}
      {a.cautions.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-body-compact text-status-warning">
          {a.cautions.map((c) => (
            <li key={c}>! {c}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2 border-t border-hairline-subtle pt-2">
        <OpenInBrain url={a.brainUrl} />
        {recheck && <RecheckButton alphaId={a.alphaId} alsoRefresh={[ADVICE_KEY]} />}
        <Button size="sm" variant="ghost" disabled={!a.expression} onClick={copy}>
          <CopyIcon />
          Copy expression
        </Button>
      </div>
    </article>
  )
}
