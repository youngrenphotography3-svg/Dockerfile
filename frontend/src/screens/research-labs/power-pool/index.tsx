/** LLM Alpha Lab: an LLM writes Power Pool or Regular Alphas for your datasets or fields; the task runs in Tasks. */

import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { fmt } from '@/lib/format'
import { useCores } from '@/lib/preferences'
import { DEFAULT_SCOPE, useScopeOptions } from '@/lib/scope'
import { useProviderLabel } from '@/screens/ai/shared'
import type { FieldFilterState } from '@/screens/data/state'
import { AddTaskButtons, useAddTask } from '@/screens/research-labs/add-task'
import {
  MAX_SIMULATIONS,
  simulationsValid,
  useLabMarket,
  useLabPreview,
} from '@/screens/research-labs/lab-task'
import { NeutralizationPicker } from '@/screens/research-labs/neutralization'
import {
  type AlphaMode,
  type PowerPoolRequest,
  powerPoolLab,
} from '@/screens/research-labs/power-pool/api'
import { FieldsPicker } from '@/screens/research-labs/power-pool/fields-picker'
import { ReviewQueue } from '@/screens/research-labs/power-pool/review-queue'
import {
  CoresSetting,
  DatasetsPanel,
  SimulationsSetting,
} from '@/screens/research-labs/task-settings'
import {
  Button,
  Chips,
  Disclosure,
  ErrorNotice,
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
} from '@/ui/kit'
import { Select } from '@/ui/overlay'

interface PowerPoolDraft {
  region: string
  delay: number
  universe: string
  datasetIds: string[]
  fieldFilter: FieldFilterState | null
  /** `null` until chosen in the form: until then Settings' default applies. */
  cores: number | null
  simulations: number | null
  model: string | null
  /** Empty keeps every neutralization BRAIN offers for the market. */
  neutralizations: string[]
  /** Empty draws from every downloaded universe of the market. */
  universes: string[]
  mode: AlphaMode
  /** Empty means every field of the chosen datasets. */
  fieldIds: string[]
  minOperators: number
  maxOperators: number
  maxFields: number
  focus: string
  review: boolean
}

const useDraft = create<PowerPoolDraft>()(
  persist(
    (): PowerPoolDraft => ({
      region: DEFAULT_SCOPE.region,
      delay: DEFAULT_SCOPE.delay,
      universe: DEFAULT_SCOPE.universe,
      datasetIds: [],
      fieldFilter: null,
      cores: null,
      simulations: null,
      model: null,
      neutralizations: [],
      universes: [],
      mode: 'power_pool',
      fieldIds: [],
      minOperators: 10,
      maxOperators: 30,
      maxFields: 4,
      focus: '',
      review: true,
    }),
    {
      name: 'alpha-harness-power-pool-lab',
      // Version 1 leaves cores unchosen, so Settings' default for new tasks applies.
      // Version 2 adds universes, none chosen: every downloaded one, as before.
      // Version 3 adds the Regular mode, hand-picked fields, a focus and the review switch.
      version: 3,
      migrate: (stored, version) => ({
        ...(stored as PowerPoolDraft),
        ...(version < 1 ? { cores: null } : {}),
        ...(version < 2 ? { universes: [] } : {}),
        ...(version < 3
          ? {
              mode: 'power_pool' as const,
              fieldIds: [],
              minOperators: 10,
              maxOperators: 30,
              maxFields: 4,
              focus: '',
              review: true,
            }
          : {}),
      }),
    },
  ),
)

const PRE =
  'num max-h-80 overflow-auto rounded-md border border-hairline bg-canvas p-3 text-body-compact whitespace-pre-wrap text-ink-muted'

export function PowerPoolLabScreen() {
  const draft = useDraft()
  const set = useDraft.setState
  const { scope, choose } = useLabMarket(draft, set, '/labs/power-pool')
  const options = useQuery({
    queryKey: ['power-pool-lab', 'options'],
    queryFn: powerPoolLab.options,
  })
  const models = options.data?.models ?? []
  const providerLabel = useProviderLabel()
  const model =
    draft.model && models.some((m) => m.ref === draft.model)
      ? draft.model
      : (options.data?.defaultModel ?? null)

  // BRAIN's legal list for this market; the LLM draws from whatever is chosen, or all of it.
  const scopeOptions = useScopeOptions({
    instrumentType: 'EQUITY',
    region: draft.region,
    delay: draft.delay,
    universe: draft.universe,
  })

  const cores = useCores(draft.cores)
  const body: PowerPoolRequest = {
    region: draft.region,
    delay: draft.delay,
    universe: draft.universe,
    dataset_ids: draft.datasetIds,
    field_filter: draft.fieldFilter ?? null,
    model,
    neutralizations: draft.neutralizations,
    universes: draft.universes,
    cores,
    simulations: draft.simulations ?? 0,
    mode: draft.mode,
    field_ids: draft.fieldIds,
    min_operators: draft.minOperators,
    max_operators: draft.maxOperators,
    max_fields: draft.maxFields,
    focus: draft.focus,
    review: draft.review,
  }
  const regular = draft.mode === 'regular'
  const { preview, current } = useLabPreview('power-pool-lab', body, powerPoolLab.preview)
  const plan = preview.data
  const maxSimulations = options.data?.maxSimulations ?? MAX_SIMULATIONS
  const add = useAddTask(() => powerPoolLab.addTask(body))
  const ready =
    plan !== undefined &&
    current &&
    plan.problems.length === 0 &&
    (!regular || draft.minOperators <= draft.maxOperators) &&
    simulationsValid(draft.simulations, maxSimulations)

  return (
    <Page>
      <PageHeader title="LLM Alpha Lab" actions={<AddTaskButtons add={add} disabled={!ready} />} />
      {options.isError && <ErrorNotice error={options.error} title="Could not load the models" />}
      {options.isSuccess && models.length === 0 && (
        <Notice
          tone="warn"
          title="This lab needs a model"
          action={
            <Button size="sm" render={<Link to="/ai/$tab" params={{ tab: 'models' }} />}>
              Set up a model
            </Button>
          }
        >
          Add a Key in LLM Integration and set up a model for it, with the limits your provider
          shows you.
        </Notice>
      )}
      <Panel title="Kind of Alpha">
        <div className="flex flex-col gap-4">
          <Segmented
            label="Kind of Alpha"
            value={draft.mode}
            onChange={(mode) => set({ mode })}
            items={[
              { value: 'power_pool', label: 'Power Pool · at most 8 operators, 3 fields' },
              { value: 'regular', label: 'Regular · a full-size Alpha with its reason' },
            ]}
          />
          {regular && (
            <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
              <Fieldset legend="Fewest different operators">
                <Input
                  type="number"
                  min={9}
                  max={40}
                  step={1}
                  aria-label="Fewest different operators"
                  className="w-28"
                  value={draft.minOperators}
                  onChange={(e) => set({ minOperators: Math.max(9, Number(e.target.value) || 10) })}
                />
              </Fieldset>
              <Fieldset legend="Most operators in all">
                <Input
                  type="number"
                  min={10}
                  max={80}
                  step={1}
                  aria-label="Most operators in all"
                  className="w-28"
                  value={draft.maxOperators}
                  onChange={(e) =>
                    set({ maxOperators: Math.max(10, Number(e.target.value) || 30) })
                  }
                />
              </Fieldset>
              <Fieldset legend="Most data fields">
                <Input
                  type="number"
                  min={1}
                  max={12}
                  step={1}
                  aria-label="Most data fields"
                  className="w-28"
                  value={draft.maxFields}
                  onChange={(e) => set({ maxFields: Math.max(1, Number(e.target.value) || 4) })}
                />
              </Fieldset>
            </div>
          )}
          <Fieldset legend="What to explore (optional)">
            <Textarea
              aria-label="What to explore"
              rows={3}
              maxLength={1000}
              placeholder="e.g. analyst estimate disagreement against growth; compare near and far horizons; avoid price-volume"
              value={draft.focus}
              onChange={(e) => set({ focus: e.target.value })}
            />
          </Fieldset>
          <div className="flex items-start gap-3">
            <Switch
              aria-label="Review before simulating"
              checked={draft.review}
              onChange={(review) => set({ review })}
            />
            <p className="text-body-compact text-pretty text-ink-subtle">
              Review before simulating. Each Alpha the LLM writes waits below for your approval,
              with its reason; only approved Alphas use your simulations.
            </p>
          </div>
        </div>
      </Panel>
      <DatasetsPanel
        ids={draft.datasetIds}
        scope={scope}
        onChoose={choose}
        filter={draft.fieldFilter}
        onClearFilter={() => set({ fieldFilter: null })}
        onRemove={(ids) => set({ datasetIds: draft.datasetIds.filter((x) => !ids.includes(x)) })}
      />
      <FieldsPicker
        scope={scope}
        datasetIds={draft.datasetIds}
        value={draft.fieldIds}
        onChange={(fieldIds) => set({ fieldIds })}
      />
      <ReviewQueue />
      <Panel title="Settings">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
            <Fieldset legend="Model">
              <Select
                label="Model"
                items={models.map((m) => ({
                  value: m.ref,
                  label: `${m.id} · ${providerLabel(m.provider)} · ${fmt.int(m.remainingToday)} left today`,
                }))}
                value={model}
                onChange={(v) => set({ model: v })}
              />
            </Fieldset>
            <CoresSetting value={cores} onChange={(next) => set({ cores: next })} />
            <SimulationsSetting
              value={draft.simulations}
              max={maxSimulations}
              placeholder="500"
              onChange={(next) => set({ simulations: next })}
            />
          </div>
          {scopeOptions.universes.length > 0 && (
            <Fieldset
              legend={draft.universes.length === 0 ? 'Universes · all downloaded' : 'Universes'}
            >
              <Chips
                label="Universes"
                items={scopeOptions.universes}
                value={draft.universes}
                onChange={(next) => set({ universes: next })}
              />
            </Fieldset>
          )}
          {scopeOptions.neutralizations.length > 0 && (
            <NeutralizationPicker
              available={scopeOptions.neutralizations}
              value={draft.neutralizations}
              onChange={(next) => set({ neutralizations: next })}
            />
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric
              boxed
              label="Datasets"
              value={fmt.int(draft.datasetIds.length)}
              hint={
                draft.fieldIds.length > 0
                  ? `${fmt.int(draft.fieldIds.length)} fields ticked`
                  : undefined
              }
            />
            <Metric boxed label="Fields" value={fmt.int(plan?.fields)} />
            <Metric
              boxed
              label="LLM Calls"
              value={fmt.int(plan?.llmCalls)}
              hint={regular ? '10 Alphas each' : '20 Alphas each'}
            />
            <Metric
              boxed
              label="Universes"
              value={fmt.int(plan?.universes.length)}
              hint={`${fmt.int(plan?.neutralizations.length)} Neutralizations`}
            />
          </div>
          <p className="text-body-compact text-pretty text-ink-subtle">
            Each Alpha gets a random Universe, Neutralization and Decay; Truncation 0.08.
          </p>
          {preview.isError && <ErrorNotice error={preview.error} title="Could not plan the task" />}
          {plan?.problems.map((m) => (
            <Notice key={m} tone="error" title={m} />
          ))}
          {plan?.warnings.map((m) => (
            <Notice key={m} tone="warn" title={m} />
          ))}
          {plan?.prompt && (
            <Disclosure summary={`Prompt · ~${fmt.int(plan.prompt.tokens)} tokens`}>
              <div className="flex flex-col gap-2">
                <pre className={PRE} role="region" aria-label="System prompt">
                  {plan.prompt.system}
                </pre>
                <pre className={PRE} role="region" aria-label="User prompt">
                  {plan.prompt.user}
                </pre>
              </div>
            </Disclosure>
          )}
        </div>
      </Panel>
    </Page>
  )
}
