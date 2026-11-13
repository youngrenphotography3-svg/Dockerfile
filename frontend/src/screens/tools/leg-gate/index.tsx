/** Leg Gate: try a template of two or more legs without paying to simulate every combination.
 *
 * Stage 1 simulates each leg alone, once per sign. Gates then decide which legs earned a place.
 * Stage 2, the combination, is simulated once, at each kept leg's winning sign, and only if
 * enough legs passed. Every threshold, weight, sign and slot value here is yours to change.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { fmt } from '@/lib/format'
import { useCores } from '@/lib/preferences'
import { useScope, useScopeOptions } from '@/lib/scope'
import { useRefetchOn } from '@/lib/ws'
import { AddTaskButtons, useAddTask } from '@/screens/research-labs/add-task'
import { CoresSetting } from '@/screens/research-labs/task-settings'
import {
  Badge,
  Button,
  Chips,
  Disclosure,
  Empty,
  ErrorNotice,
  Field,
  Fieldset,
  Input,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Segmented,
  Switch,
  Textarea,
  type Tone,
} from '@/ui/kit'
import { Select } from '@/ui/overlay'
import { ScopePicker } from '@/ui/scope-picker'
import {
  DEFAULT_GATES,
  type Evaluation,
  EXAMPLE_TEMPLATE,
  EXAMPLE_VALUES,
  type Gates,
  legGate,
  type Plan,
  type Row,
  type Run,
  type StageOneJudgement,
  type StageTwoJudgement,
} from './api'

const list = (text: string): number[] =>
  text
    .split(/[\s,]+/)
    .map(Number)
    .filter((n) => Number.isFinite(n) && text.trim() !== '')

const num = (text: string, fallback: number): number => {
  const n = Number(text)
  return text.trim() !== '' && Number.isFinite(n) ? n : fallback
}

const VERDICT_TONE: Record<string, Tone> = {
  keep: 'profit',
  drop: 'loss',
  waiting: 'muted',
  pass: 'profit',
  fail: 'loss',
}

/** The gate thresholds as text, so a half-typed number is not rewritten under the cursor. */
interface GateText {
  legMinSharpe: string
  requiredYears: string
  yearMinSharpe: string
  minLegs: string
  weights: string
  comboMinSharpe: string
  comboMinRecentSharpe: string
  subUniverseMargin: string
}

const GATE_TEXT: GateText = {
  legMinSharpe: String(DEFAULT_GATES.legMinSharpe),
  requiredYears: DEFAULT_GATES.requiredYears.join(', '),
  yearMinSharpe: String(DEFAULT_GATES.yearMinSharpe),
  minLegs: String(DEFAULT_GATES.minLegs),
  weights: DEFAULT_GATES.weights.join(', '),
  comboMinSharpe: String(DEFAULT_GATES.comboMinSharpe),
  comboMinRecentSharpe: String(DEFAULT_GATES.comboMinRecentSharpe),
  subUniverseMargin: String(DEFAULT_GATES.subUniverseMargin * 100),
}

function gatesOf(t: GateText): Gates {
  return {
    legMinSharpe: num(t.legMinSharpe, DEFAULT_GATES.legMinSharpe),
    requiredYears: list(t.requiredYears).map(Math.round),
    yearMinSharpe: num(t.yearMinSharpe, 0),
    minLegs: Math.max(1, Math.round(num(t.minLegs, DEFAULT_GATES.minLegs))),
    weights: list(t.weights).filter((w) => w > 0),
    comboMinSharpe: num(t.comboMinSharpe, DEFAULT_GATES.comboMinSharpe),
    comboMinRecentSharpe: num(t.comboMinRecentSharpe, DEFAULT_GATES.comboMinRecentSharpe),
    subUniverseMargin: num(t.subUniverseMargin, 10) / 100,
  }
}

function RowTable({ rows }: { rows: Row[] }) {
  return (
    <table className="w-full text-body">
      <thead className="text-left text-ink-subtle">
        <tr>
          <th className="py-1">Sign</th>
          <th>Sharpe</th>
          <th>Years</th>
          <th>Result</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.leg}${r.sign}`} className="border-t border-hairline align-top">
            <td className="mono-metric py-1.5">{r.sign > 0 ? '+1' : '-1'}</td>
            <td className="mono-metric">
              {r.sharpe === null ? r.state.toLowerCase() : fmt.ratio(r.sharpe)}
            </td>
            <td className="mono-metric">
              {Object.entries(r.years)
                .map(([y, v]) => `${y}: ${fmt.ratio(v)}`)
                .join('  ')}
            </td>
            <td>
              {r.passed === undefined ? null : r.passed ? (
                <Badge tone="profit">passes</Badge>
              ) : (
                <span className="text-ink-subtle">{(r.reasons ?? []).join('; ')}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function StageOne({ ev, judgement }: { ev: Evaluation; judgement: StageOneJudgement }) {
  const queryClient = useQueryClient()
  const queue = useMutation({
    mutationFn: () => legGate.stageTwo(ev.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tools', 'leg-gate'] }),
  })
  return (
    <div className="flex flex-col gap-3">
      <Notice tone={judgement.go || judgement.waiting ? 'info' : 'warn'}>
        {judgement.summary}
        {ev.child !== null && ` Stage 2 is task ${ev.child}.`}
      </Notice>
      {judgement.legs.map((l) => (
        <div key={l.leg} className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">{l.leg}</span>
            <Badge tone={VERDICT_TONE[l.verdict] ?? 'muted'}>{l.verdict}</Badge>
          </div>
          <RowTable rows={l.results} />
        </div>
      ))}
      {ev.canQueueStage2 && (
        <div>
          <Button variant="primary" loading={queue.isPending} onClick={() => queue.mutate()}>
            Queue stage 2 now
          </Button>
        </div>
      )}
      {queue.isError && <ErrorNotice error={queue.error} />}
    </div>
  )
}

function StageTwo({ judgement, row }: { judgement: StageTwoJudgement; row: Row | undefined }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="font-medium">Combination</span>
        <Badge tone={VERDICT_TONE[judgement.verdict] ?? 'muted'}>{judgement.verdict}</Badge>
        {row?.alphaId && <span className="mono-metric text-ink-subtle">{row.alphaId}</span>}
      </div>
      {judgement.gates.map((g) => (
        <div key={g.gate} className="flex items-center gap-2 text-body">
          <Badge tone={g.ok ? 'profit' : 'loss'}>{g.ok ? 'met' : 'missed'}</Badge>
          <span>{g.gate}</span>
          <span className="mono-metric text-ink-subtle">{g.detail}</span>
        </div>
      ))}
    </div>
  )
}

function RunCard({ run }: { run: Run }) {
  const key = ['tools', 'leg-gate', run.id] as const
  const query = useQuery({
    queryKey: key,
    queryFn: () => legGate.evaluation(run.id),
    refetchInterval: run.status === 'COMPLETE' || run.status === 'FAILED' ? false : 15_000,
  })
  useRefetchOn('studies', key, 5_000)
  const ev = query.data
  return (
    <Disclosure
      summary={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{run.name}</span>
          <Badge tone="outline">{run.stage === 1 ? 'Stage 1' : 'Stage 2'}</Badge>
          <Badge tone={run.status === 'COMPLETE' ? 'profit' : 'muted'}>
            {run.status.toLowerCase()}
          </Badge>
          {run.message && <span className="text-body text-ink-subtle">{run.message}</span>}
        </span>
      }
    >
      <div className="p-3">
        {query.isError ? (
          <ErrorNotice error={query.error} />
        ) : !ev ? null : ev.stage === 1 ? (
          <StageOne ev={ev} judgement={ev.judgement as StageOneJudgement} />
        ) : (
          <StageTwo judgement={ev.judgement as StageTwoJudgement} row={ev.rows[0]} />
        )}
      </div>
    </Disclosure>
  )
}

function Runs() {
  const query = useQuery({ queryKey: ['tools', 'leg-gate', 'runs'], queryFn: legGate.runs })
  useRefetchOn('studies', ['tools', 'leg-gate', 'runs'], 5_000)
  return (
    <Panel
      title="Runs"
      description="Each Leg Gate task, with its gates applied to whatever has finished. Stage 2 appears here as its own task once the gates let it through."
    >
      {query.isError ? (
        <ErrorNotice error={query.error} />
      ) : !query.data?.length ? (
        <Empty title="No runs yet">Queue stage 1 above and it shows here.</Empty>
      ) : (
        <div className="flex flex-col gap-2">
          {query.data.map((r) => (
            <RunCard key={r.id} run={r} />
          ))}
        </div>
      )}
    </Panel>
  )
}

export function LegGateScreen() {
  const [template, setTemplate] = useState(EXAMPLE_TEMPLATE)
  const [values, setValues] = useState<Record<string, string>>(EXAMPLE_VALUES)
  const [legs, setLegs] = useState<string[]>([])
  const [combine, setCombine] = useState<string | null>(null)
  const [weightSlots, setWeightSlots] = useState<Record<string, string>>({})
  const [signs, setSigns] = useState<number[]>([1, -1])
  const [scope, setScope] = useScope('leg-gate')
  const [neutralization, setNeutralization] = useState('NONE')
  const [decay, setDecay] = useState('0')
  const [truncation, setTruncation] = useState('0.08')
  const [gateText, setGateText] = useState<GateText>(GATE_TEXT)
  const [auto, setAuto] = useState(true)
  const [chosenCores, setCores] = useState<number | null>(null)
  const cores = useCores(chosenCores)
  const options = useScopeOptions(scope)

  const plan: Plan = useMemo(
    () => ({
      template,
      values,
      legs,
      combine,
      weightSlots,
      signs,
      settings: {
        region: scope.region,
        delay: scope.delay,
        universe: scope.universe,
        neutralization,
        decay: Math.max(0, Math.round(num(decay, 0))),
        truncation: num(truncation, 0.08),
      },
      gates: gatesOf(gateText),
      autoContinue: auto,
    }),
    [
      template,
      values,
      legs,
      combine,
      weightSlots,
      signs,
      scope,
      neutralization,
      decay,
      truncation,
      gateText,
      auto,
    ],
  )

  // Preview on a pause in typing: it parses the template and builds every staged expression,
  // and costs no simulation.
  const [settled, setSettled] = useState(plan)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(plan), 350)
    return () => clearTimeout(timer)
  }, [plan])
  const preview = useQuery({
    queryKey: ['tools', 'leg-gate', 'preview', settled],
    queryFn: () => legGate.preview(settled),
    placeholderData: (previous) => previous,
  })
  const found = preview.data

  // Offer sensible starting choices once the template has been read: names ending `_leg` as
  // the legs, the assignment that reads them as the combine, `w_<stem>` as each weight slot.
  // Only ever fills what is still empty, so nothing the user chose is overwritten.
  useEffect(() => {
    if (!found) return
    if (legs.length === 0) {
      const guess = found.assigned.filter((n) => n.endsWith('_leg'))
      if (guess.length >= 2) setLegs(guess)
    }
    if (combine === null && found.combineCandidates.length === 1)
      setCombine(found.combineCandidates[0] ?? null)
    const filled = { ...weightSlots }
    let changed = false
    for (const leg of legs) {
      const guess = `w_${leg.replace(/_leg$/, '')}`
      if (!filled[leg] && found.slots.includes(guess)) {
        filled[leg] = guess
        changed = true
      }
    }
    if (changed) setWeightSlots(filled)
  }, [found, legs, combine, weightSlots])

  const weightNames = new Set(Object.values(weightSlots))
  const valueSlots = (found?.slots ?? []).filter((s) => !weightNames.has(s))
  const blocked = !found || found.problems.length > 0 || found.missing.length > 0

  const add = useAddTask(() => legGate.start(plan, cores))
  const gate = (key: keyof GateText) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setGateText((t) => ({ ...t, [key]: e.target.value }))

  return (
    <Page>
      <PageHeader
        title="Leg Gate"
        description="Try a template of two or more legs in stages: each leg alone first, the combination only if enough legs earn it."
        actions={<AddTaskButtons add={add} disabled={blocked} />}
      />

      <Panel
        title="Template"
        description="Write each leg as its own assignment, then the combine that sums them, then the final line. Anything written $name is a slot you give a value below."
        actions={
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setTemplate(EXAMPLE_TEMPLATE)
              setValues(EXAMPLE_VALUES)
              setLegs([])
              setCombine(null)
              setWeightSlots({})
            }}
          >
            Load example
          </Button>
        }
      >
        <Textarea
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          spellCheck={false}
          className="mono-metric min-h-40"
          aria-label="Template"
        />
      </Panel>

      <Panel
        title="Legs"
        description="A leg is an assignment that carries one dataset's signal. The combine is the assignment that adds them; its weight slots are where stage 2 puts each leg's signed weight."
      >
        <div className="flex flex-col gap-4">
          <Fieldset legend="Legs">
            <Chips
              label="Legs"
              items={(found?.assigned ?? []).map((n) => ({ value: n, label: n }))}
              value={legs}
              onChange={setLegs}
            />
          </Fieldset>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Combine">
              <Select
                label="Combine"
                value={combine}
                onChange={setCombine}
                mono
                items={(found?.assigned ?? [])
                  .filter((n) => !legs.includes(n))
                  .map((n) => ({ value: n, label: n }))}
              />
            </Field>
            {legs.map((leg) => (
              <Field key={leg} label={`Weight slot for ${leg}`}>
                <Select
                  label={`Weight slot for ${leg}`}
                  value={weightSlots[leg] ?? null}
                  onChange={(slot) => setWeightSlots((w) => ({ ...w, [leg]: slot }))}
                  mono
                  items={(found?.slots ?? []).map((s) => ({ value: s, label: `$${s}` }))}
                />
              </Field>
            ))}
          </div>
        </div>
      </Panel>

      <Panel
        title="Slot values"
        description="A value for every slot the staged expressions read. Datasets, windows, groups, decay: change any of them."
      >
        {valueSlots.length === 0 ? (
          <Empty title="No slots">Write $name in the template to add one.</Empty>
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            {valueSlots.map((slot) => (
              <Field key={slot} label={`$${slot}`}>
                <Input
                  value={values[slot] ?? ''}
                  onChange={(e) => setValues((v) => ({ ...v, [slot]: e.target.value }))}
                  spellCheck={false}
                  className="num"
                  aria-invalid={found?.missing.includes(slot) || undefined}
                />
              </Field>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="Simulation settings"
        description="Held the same for every stage, so a leg is judged under the settings the combination will use."
      >
        <div className="flex flex-col gap-4">
          <ScopePicker scope={scope} onChange={setScope} />
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Neutralization">
              <Select
                label="Neutralization"
                value={neutralization}
                onChange={setNeutralization}
                mono
                items={options.neutralizations}
              />
            </Field>
            <Field label="Decay (setting)">
              <Input
                value={decay}
                onChange={(e) => setDecay(e.target.value)}
                inputMode="numeric"
                className="num"
              />
            </Field>
            <Field label="Truncation (setting)">
              <Input
                value={truncation}
                onChange={(e) => setTruncation(e.target.value)}
                inputMode="decimal"
                className="num"
              />
            </Field>
          </div>
          <Fieldset
            legend="Signs to try for each leg"
            hint="Each leg is simulated once per sign. Try one sign if you already know the direction."
          >
            <Segmented
              label="Signs"
              value={signs.length === 2 ? 0 : (signs[0] ?? 1)}
              onChange={(v) => setSigns(v === 0 ? [1, -1] : [v])}
              items={[
                { value: 0, label: '+1 and -1' },
                { value: 1, label: '+1 only' },
                { value: -1, label: '-1 only' },
              ]}
            />
          </Fieldset>
        </div>
      </Panel>

      <Panel
        title="Gates"
        description="A leg stays only if one sign clears stage 1. The combination is judged by stage 2. Every figure below is an example; set your own."
      >
        <div className="flex flex-col gap-4">
          <div>
            <h3 className="mb-2 text-body font-medium">Stage 1: each leg alone</h3>
            <div className="grid gap-3 sm:grid-cols-4">
              <Field label="Minimum Sharpe">
                <Input
                  value={gateText.legMinSharpe}
                  onChange={gate('legMinSharpe')}
                  inputMode="decimal"
                  className="num"
                />
              </Field>
              <Field label="Years that must earn" hint="Comma separated">
                <Input
                  value={gateText.requiredYears}
                  onChange={gate('requiredYears')}
                  className="num"
                />
              </Field>
              <Field label="Each year's Sharpe above">
                <Input
                  value={gateText.yearMinSharpe}
                  onChange={gate('yearMinSharpe')}
                  inputMode="decimal"
                  className="num"
                />
              </Field>
              <Field label="Legs that must pass">
                <Input
                  value={gateText.minLegs}
                  onChange={gate('minLegs')}
                  inputMode="numeric"
                  className="num"
                />
              </Field>
            </div>
          </div>
          <div>
            <h3 className="mb-2 text-body font-medium">Stage 2: the combination</h3>
            <div className="grid gap-3 sm:grid-cols-4">
              <Field label="Weights, strongest leg first" hint="Last one repeats for further legs">
                <Input value={gateText.weights} onChange={gate('weights')} className="num" />
              </Field>
              <Field label="Minimum Sharpe">
                <Input
                  value={gateText.comboMinSharpe}
                  onChange={gate('comboMinSharpe')}
                  inputMode="decimal"
                  className="num"
                />
              </Field>
              <Field label="Minimum last-2-year Sharpe">
                <Input
                  value={gateText.comboMinRecentSharpe}
                  onChange={gate('comboMinRecentSharpe')}
                  inputMode="decimal"
                  className="num"
                />
              </Field>
              <Field label="Sub-universe margin over cutoff (%)">
                <Input
                  value={gateText.subUniverseMargin}
                  onChange={gate('subUniverseMargin')}
                  inputMode="decimal"
                  className="num"
                />
              </Field>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Switch checked={auto} onChange={setAuto} aria-label="Queue stage 2 automatically" />
            <span className="text-body">
              Queue stage 2 automatically when stage 1 finishes and the gates pass.
              <span className="text-ink-subtle">
                {' '}
                Off: it waits for you to press the button on the run.
              </span>
            </span>
          </div>
        </div>
      </Panel>

      <Panel
        title="Plan and cost"
        description="Free to read: nothing is simulated until you add the task, and stage 2 only if the gates allow it."
        actions={<CoresSetting value={cores} onChange={setCores} />}
      >
        {preview.isError ? (
          <ErrorNotice error={preview.error} />
        ) : !found ? null : (
          <div className="flex flex-col gap-3">
            {found.problems.map((p) => (
              <Notice key={p} tone="error" title={p} />
            ))}
            {found.missing.length > 0 && (
              <Notice tone="warn" title="Give a value for">
                {found.missing.map((m) => `$${m}`).join(', ')}
              </Notice>
            )}
            {found.quota && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Metric
                  label="Stage 1 simulations"
                  value={fmt.int(found.quota.stageOne)}
                  hint="always run"
                />
                <Metric
                  label="Stage 2"
                  value={fmt.int(found.quota.stageTwo)}
                  hint="only if the gates pass"
                />
                <Metric label="Most this run spends" value={fmt.int(found.quota.mostSpent)} />
                <Metric
                  label="Every sign pattern instead"
                  value={fmt.int(found.quota.everySignPattern)}
                  hint="combinations, no gating"
                />
              </div>
            )}
            {found.sims.length > 0 && (
              <Disclosure summary={`Stage 1 expressions (${found.sims.length})`}>
                <div className="flex flex-col gap-3 p-3">
                  {found.sims.map((s) => (
                    <div key={`${s.leg}${s.sign}`}>
                      <div className="mb-1 text-body font-medium">
                        {s.leg} at {s.sign > 0 ? '+1' : '-1'}
                      </div>
                      <pre className="mono-metric whitespace-pre-wrap text-ink-subtle">
                        {s.expression}
                      </pre>
                    </div>
                  ))}
                  {found.combined && (
                    <div>
                      <div className="mb-1 text-body font-medium">
                        Stage 2, shown at +1 until the gates choose the signs
                      </div>
                      <pre className="mono-metric whitespace-pre-wrap text-ink-subtle">
                        {found.combined}
                      </pre>
                    </div>
                  )}
                </div>
              </Disclosure>
            )}
          </div>
        )}
        {add.isError && <ErrorNotice error={add.error} />}
      </Panel>

      <Runs />
    </Page>
  )
}
