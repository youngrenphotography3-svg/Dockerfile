/** Base Payment: what BRAIN's base-payment articles say to do, read off your own account.
 *
 * Nothing here predicts a payment: the articles name the levers (4 Alphas a day, diversity,
 * margin above 4 bps, Osmosis, theme multipliers) but not the formula. Each panel is a count,
 * a share or an average against a threshold the articles give. Read-only, except the
 * robustness button, which queues two simulations.
 */

import { useQuery } from '@tanstack/react-query'
import { DownloadIcon } from 'lucide-react'
import { saveText } from '@/lib/download'
import { fmt } from '@/lib/format'
import { useRefetchOn } from '@/lib/ws'
import { OpenInBrain } from '@/screens/pool/shared'
import { useAddTask } from '@/screens/research-labs/add-task'
import {
  Badge,
  Button,
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
import {
  BASE_PAYMENT_KEY,
  type BasePaymentReport,
  basePayment,
  type ChecklistItem,
  type Growth,
  type OsmosisScope,
  osmosisCsv,
} from './api'

const STATUS_TONE: Record<ChecklistItem['status'], Tone> = {
  ok: 'profit',
  warn: 'warn',
  todo: 'muted',
}
const STATUS_WORD: Record<ChecklistItem['status'], string> = {
  ok: 'Done',
  warn: 'Watch',
  todo: 'To do',
}
const TREND_TONE: Record<Growth['trend'], Tone> = {
  improving: 'profit',
  flat: 'neutral',
  declining: 'loss',
  new: 'muted',
}
const AXIS_LABEL: Record<string, string> = {
  region: 'Region',
  delay: 'Delay',
  universe: 'Universe',
  neutralization: 'Neutralization',
  turnover: 'Turnover',
  category: 'Data category',
}

const capTone = (have: number, cap: number): Tone =>
  have >= cap ? 'profit' : have > 0 ? 'neutral' : 'warn'

function RobustnessButton({ alphaId }: { alphaId: string }) {
  const add = useAddTask(() => basePayment.robustness(alphaId))
  return (
    <Button size="sm" variant="secondary" loading={add.isPending} onClick={() => add.mutate(true)}>
      Check rank / sign
    </Button>
  )
}

function Quantity({ report }: { report: BasePaymentReport }) {
  const q = report.quantity
  const peak = Math.max(1, ...q.recent.map((d) => d.count))
  return (
    <Panel
      title="Quantity"
      description="Base payment accrues daily, so the allowance is worth using every day: up to 4 Alphas and 1 SuperAlpha a day, and 5 Delay 0 Alphas a month."
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Metric
          label="Alphas today"
          value={`${q.submittedToday} / ${q.alphaCap}`}
          tone={capTone(q.submittedToday, q.alphaCap)}
        />
        <Metric
          label="SuperAlpha today"
          value={`${q.superToday} / ${q.superCap}`}
          tone={capTone(q.superToday, q.superCap)}
        />
        <Metric
          label="Delay 0 this month"
          value={`${q.d0Month} / ${q.d0Cap}`}
          hint={`${q.d0Remaining} left`}
        />
        <Metric
          label="Daily streak"
          value={`${q.streak} d`}
          tone={q.streak > 0 ? 'profit' : 'warn'}
        />
        <Metric label="Last 7 days" value={fmt.int(q.lastSevenDays)} hint="of 28 possible" />
      </div>
      <div
        className="mt-4 flex h-14 items-end gap-1"
        role="img"
        aria-label="Alphas submitted each of the last 14 days"
      >
        {q.recent.map((d) => (
          <div
            key={d.date}
            className="flex flex-1 flex-col items-center gap-1"
            title={`${d.date}: ${d.count}`}
          >
            <div
              className={
                d.count >= q.alphaCap
                  ? 'w-full rounded-xs bg-profit'
                  : d.count
                    ? 'w-full rounded-xs bg-ink-subtle'
                    : 'w-full rounded-xs bg-hairline'
              }
              style={{ height: `${Math.max(4, (d.count / peak) * 40)}px` }}
            />
          </div>
        ))}
      </div>
    </Panel>
  )
}

function Today({ report }: { report: BasePaymentReport }) {
  const { plan } = report
  return (
    <Panel
      title="Submit today"
      description="Your Submit Advisor's recommendations, spread over markets so the day's picks aren't all one region, and held to the Delay 0 allowance. Nothing is submitted for you."
    >
      {plan.slots === 0 ? (
        <Notice tone="info">The daily allowance of Alphas is used.</Notice>
      ) : plan.picks.length === 0 ? (
        <Empty title="Nothing recommended yet">
          Run labs and let BRAIN finish its checks; recommended Alphas appear here.
        </Empty>
      ) : (
        <ul className="flex flex-col divide-y divide-hairline">
          {plan.picks.map((p, i) => (
            <li key={p.alphaId} className="flex flex-wrap items-center gap-3 py-2">
              <span className="mono-metric w-5 text-ink-subtle">{i + 1}</span>
              <span className="mono-metric">{p.alphaId}</span>
              <Badge tone="outline">{p.scope}</Badge>
              <span className="min-w-0 flex-1 text-body text-ink-subtle">
                {p.reasons.join(' · ')}
              </span>
              <RobustnessButton alphaId={p.alphaId} />
              {p.brainUrl && <OpenInBrain url={p.brainUrl} />}
            </li>
          ))}
        </ul>
      )}
      {plan.short > 0 && (
        <Notice
          tone="warn"
          title={`${plan.short} slot${plan.short === 1 ? '' : 's'} unfilled`}
          className="mt-3"
        >
          <ul className="list-disc pl-4">
            {plan.tips.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </Notice>
      )}
      <p className="mt-3 text-body text-ink-subtle">
        {plan.superAlphaOpen
          ? "Today's SuperAlpha is still open."
          : "Today's SuperAlpha is submitted."}{' '}
        The robustness button queues rank(Alpha) and sign(Alpha) at the Alpha's own settings (two
        simulations): the articles advise confirming an Alpha still performs under both before it
        goes in.
      </p>
    </Panel>
  )
}

function Diversity({ report }: { report: BasePaymentReport }) {
  const d = report.diversity
  return (
    <Panel
      title="Diversity"
      description={`Share of your last ${d.windowDays} days' submissions (${d.considered}) on each axis. The Value Factor may suffer when one region, delay, turnover band or dataset holds more than half.`}
    >
      {d.considered === 0 ? (
        <Empty title="No recent submissions">Submit some Alphas and the spread shows here.</Empty>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {Object.entries(d.axes).map(([axis, items]) => (
            <div key={axis} className="flex flex-col gap-1.5">
              <h3 className="text-body font-medium text-ink">{AXIS_LABEL[axis] ?? axis}</h3>
              {items.slice(0, 6).map((s) => (
                <div key={s.name} className="flex items-center gap-2" title={`${s.count} Alphas`}>
                  <span className="w-28 truncate text-body text-ink-subtle">{s.name}</span>
                  <div className="h-2 flex-1 rounded-xs bg-hairline">
                    <div
                      className={s.over ? 'h-2 rounded-xs bg-warn' : 'h-2 rounded-xs bg-ink-subtle'}
                      style={{ width: `${Math.round(s.share * 100)}%` }}
                    />
                  </div>
                  <span className="mono-metric w-12 text-right">{fmt.pct(s.share, 0)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      {d.flags.length > 0 && (
        <Notice tone="warn" title="Concentrated" className="mt-3">
          <ul className="list-disc pl-4">
            {d.flags.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </Notice>
      )}
    </Panel>
  )
}

function GrowthPanel({ report }: { report: BasePaymentReport }) {
  return (
    <Panel
      title="Self-Growth"
      description="Per region, the last 30 days of submissions against the 30 before. The articles ask whether quality is trending up: higher Sharpe, lower correlation."
    >
      {report.growth.length === 0 ? (
        <Empty title="Not enough history">Needs submissions in the last 60 days.</Empty>
      ) : (
        <table className="w-full text-body">
          <thead className="text-left text-ink-subtle">
            <tr>
              <th className="py-1">Region</th>
              <th>Submitted (now / before)</th>
              <th>Sharpe</th>
              <th>Prod corr</th>
              <th>Margin</th>
              <th>Trend</th>
            </tr>
          </thead>
          <tbody>
            {report.growth.map((g) => (
              <tr key={g.region} className="border-t border-hairline">
                <td className="py-1.5 font-medium">{g.region}</td>
                <td className="mono-metric">
                  {g.recent.count} / {g.previous.count}
                </td>
                <td className="mono-metric">
                  {fmt.ratio(g.recent.sharpe)} / {fmt.ratio(g.previous.sharpe)}
                </td>
                <td className="mono-metric">
                  {fmt.ratio(g.recent.prodCorrelation)} / {fmt.ratio(g.previous.prodCorrelation)}
                </td>
                <td className="mono-metric">{fmt.bps(g.recent.margin)}</td>
                <td>
                  <Badge tone={TREND_TONE[g.trend]}>{g.trend}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}

function ScopeTable({ scope }: { scope: OsmosisScope }) {
  return (
    <Disclosure
      summary={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{scope.scope}</span>
          <Badge tone={scope.ready ? 'profit' : 'warn'}>
            {scope.ready ? `${scope.alphas.length} Alphas` : `needs ${scope.needed} more`}
          </Badge>
          <span className="text-ink-subtle">{scope.submitted} submitted</span>
        </span>
      }
    >
      {scope.ready ? (
        <div className="flex flex-col gap-2 p-3">
          <table className="w-full text-body">
            <thead className="text-left text-ink-subtle">
              <tr>
                <th>Alpha</th>
                <th>Points</th>
                <th>Sharpe</th>
                <th>Turnover</th>
                <th>Margin</th>
              </tr>
            </thead>
            <tbody>
              {scope.alphas.map((a) => (
                <tr
                  key={a.alphaId}
                  className="border-t border-hairline"
                  title={a.note || undefined}
                >
                  <td className="mono-metric py-1">{a.alphaId}</td>
                  <td className="mono-metric">{fmt.int(a.points)}</td>
                  <td className="mono-metric">{fmt.ratio(a.sharpe)}</td>
                  <td className="mono-metric">{fmt.pct(a.turnover, 0)}</td>
                  <td className="mono-metric">{fmt.bps(a.margin)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {scope.dropped.length > 0 && (
            <p className="text-body text-ink-subtle">
              Left out: {scope.dropped.map((d) => `${d.alphaId} (${d.reason})`).join('; ')}
            </p>
          )}
        </div>
      ) : (
        <p className="p-3 text-body text-ink-subtle">
          Osmosis needs at least {10} Alphas in a scope. Submit {scope.needed} more here to make it
          count.
        </p>
      )}
    </Disclosure>
  )
}

function OsmosisPanel({ report }: { report: BasePaymentReport }) {
  const o = report.osmosis
  return (
    <Panel
      title="Osmosis"
      description="An active allocation earned consistently higher daily base payouts, as a quality multiplier of 1 to 2. It needs 3 scopes (region x delay), each with exactly 100,000 points over at least 10 Alphas."
      actions={
        <Button
          size="sm"
          variant="secondary"
          disabled={o.readyScopes === 0}
          onClick={() => saveText('osmosis-allocation.csv', osmosisCsv(o), 'text/csv')}
        >
          <DownloadIcon className="size-3.5" /> Download CSV
        </Button>
      }
    >
      <Notice tone={o.valid ? 'info' : 'warn'}>
        {o.readyScopes} of {o.requiredScopes} required scopes are ready. Allocations lock{' '}
        {fmt.dateTime(o.nextLock)} (Sunday night, New York) and count a week later. BRAIN takes the
        points only through its own Alphas table, so copy them across from the CSV; nothing is sent
        from here.
      </Notice>
      <div className="mt-3 flex flex-col gap-2">
        {o.scopes.length === 0 ? (
          <Empty title="No submitted Alphas yet" />
        ) : (
          o.scopes.map((s) => <ScopeTable key={s.scope} scope={s} />)
        )}
      </div>
      <p className="mt-3 text-body text-ink-subtle">
        Points follow Sharpe, discounted for turnover over 20% and margin under 4 bps, capped at 15%
        an Alpha, with the weaker of any pair moving together (PnL correlation of 0.70 or more) left
        out. The guide also says to drop Alphas that lose about 60% of Sharpe under investability
        limits; that figure isn't held here, so check it on BRAIN.
      </p>
    </Panel>
  )
}

function Checklist({ report }: { report: BasePaymentReport }) {
  return (
    <Panel
      title="Checklist"
      description="The articles' one-page checklist, answered from your account."
    >
      <ul className="flex flex-col divide-y divide-hairline">
        {report.checklist.map((c) => (
          <li key={c.label} className="flex flex-wrap items-center gap-3 py-2">
            <Badge tone={STATUS_TONE[c.status]}>{STATUS_WORD[c.status]}</Badge>
            <span className="font-medium text-ink">{c.label}</span>
            <span className="text-body text-ink-subtle">{c.detail}</span>
          </li>
        ))}
      </ul>
      {!report.multipliersKnown && (
        <p className="mt-2 text-body text-ink-subtle">
          BRAIN's pyramid multipliers could not be read just now, so theme alignment is not scored.
        </p>
      )}
    </Panel>
  )
}

export function BasePaymentScreen() {
  const query = useQuery({ queryKey: BASE_PAYMENT_KEY, queryFn: basePayment.report })
  useRefetchOn('studies', BASE_PAYMENT_KEY, 30_000)
  const report = query.data
  return (
    <Page>
      <PageHeader
        title="Base Payment"
        description="The levers BRAIN's base-payment articles name, read off your own submissions: daily quantity, diversity, growth, Osmosis and today's picks."
        actions={
          <Button size="sm" variant="secondary" onClick={() => query.refetch()}>
            Refresh
          </Button>
        }
      />
      {query.isError ? (
        <ErrorNotice error={query.error} />
      ) : !report ? (
        <Skeleton className="h-64" />
      ) : (
        <>
          <Quantity report={report} />
          <Today report={report} />
          <Checklist report={report} />
          <Diversity report={report} />
          <GrowthPanel report={report} />
          <OsmosisPanel report={report} />
          <Disclosure summary="How base payment is built, from the articles">
            <div className="flex flex-col gap-2 p-3 text-body text-ink-subtle">
              <p>
                Five components: Quantity (volume), Quality (diverse, theme-matching Alphas with low
                correlation), Self-Growth (improving over time), Scaling (your productivity relative
                to other consultants) and the Value Factor (what your Alphas add to a combo out of
                sample).
              </p>
              <p>
                The articles give no formula and no amounts, so this screen reports figures and
                thresholds, not a payment estimate. Submit Advisor scores now carry a few points for
                the same levers: a pyramid multiplier above 1x, margin below 4 bps, under 70% Sharpe
                kept in the sub-universe, and concentration. They rank; they never override a check.
              </p>
              <p>
                Not here: do not pre-mix uncorrelated Alphas into one, and never add noise to pass a
                correlation test. Those are habits, not figures.
              </p>
            </div>
          </Disclosure>
        </>
      )}
    </Page>
  )
}
