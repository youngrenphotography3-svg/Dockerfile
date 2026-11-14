/** Leg Gate: try a multi-leg template in stages, so the combination is simulated only when its legs earned it. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

export interface Gates {
  legMinSharpe: number
  requiredYears: number[]
  yearMinSharpe: number
  minLegs: number
  weights: number[]
  comboMinSharpe: number
  comboMinRecentSharpe: number
  subUniverseMargin: number
}

export interface PlanSettings {
  region: string
  delay: number
  universe: string
  neutralization: string
  decay: number
  truncation: number
}

export interface Plan {
  template: string
  values: Record<string, string>
  legs: string[]
  combine: string | null
  weightSlots: Record<string, string>
  signs: number[]
  settings: PlanSettings
  gates: Gates
  autoContinue: boolean
}

export interface Quota {
  stageOne: number
  stageTwo: number
  mostSpent: number
  leastSpent: number
  everySignPattern: number
}

export interface Preview {
  problems: string[]
  assigned: string[]
  slots: string[]
  combineCandidates: string[]
  sims: { leg: string; sign: number; expression: string }[]
  combined: string | null
  missing: string[]
  quota: Quota | null
}

export interface SubUniverse {
  result?: string
  value?: number
  limit?: number
}

export interface Row {
  leg: string
  sign: number
  state: string
  alphaId: string | null
  expression: string
  sharpe: number | null
  years: Record<string, number>
  recentSharpe: number | null
  subUniverse: SubUniverse | null
  passed?: boolean
  reasons?: string[]
}

export interface LegVerdict {
  leg: string
  results: Row[]
  winner: number | null
  verdict: 'keep' | 'drop' | 'waiting'
}

export interface StageOneJudgement {
  legs: LegVerdict[]
  kept: { leg: string; sign: number; sharpe: number | null }[]
  waiting: boolean
  go: boolean
  summary: string
  avoidedSimulations: number
}

export interface StageTwoJudgement {
  gates: { gate: string; ok: boolean; detail: string }[]
  verdict: 'pass' | 'fail' | 'waiting'
}

export interface Evaluation {
  id: number
  name: string
  stage: 1 | 2
  status: string
  parent: number | null
  child: number | null
  gates: Gates
  rows: Row[]
  judgement: StageOneJudgement | StageTwoJudgement
  canQueueStage2: boolean
}

export interface Run {
  id: number
  name: string
  stage: number
  parent: number | null
  status: string
  message: string | null
}

const L = '/api/tools/leg-gate'
type Added = components['schemas']['AddedTask']

export const legGate = {
  /** Free: parses the template and builds every staged expression; simulates nothing. */
  preview: (plan: Plan) => http.post<Preview>(`${L}/preview`, plan),
  /** Queues stage 1 only: each leg alone, at each sign. */
  start: (plan: Plan, cores: number) => http.post<Added>(`${L}/tasks`, { ...plan, cores }),
  runs: () => http.get<Run[]>(`${L}/runs`),
  evaluation: (id: number) => http.get<Evaluation>(`${L}/${id}`),
  stageTwo: (id: number) => http.post<Added>(`${L}/${id}/stage-two`, {}),
}

export const EXAMPLE_TEMPLATE = `fundamental6_leg = group_rank(ts_zscore(ts_backfill($fundamental6, $bf_slow), $win_slow), $group);
other335_leg = group_rank(ts_zscore(ts_backfill($other335, $bf_fast), $win_fast), $group);
raw = add(multiply($w_other335, other335_leg), multiply($w_fundamental6, fundamental6_leg), filter=true);
group_neutralize(ts_decay_linear(truncate(raw, maxPercent=$trunc), $decay), $country)`

/** Starting values for the example's slots; every one is yours to change. */
export const EXAMPLE_VALUES: Record<string, string> = {
  fundamental6: 'fnd6_newa1_emp',
  bf_slow: '63',
  win_slow: '252',
  bf_fast: '5',
  win_fast: '63',
  group: 'sector',
  trunc: '0.08',
  decay: '5',
  country: 'country',
}

export const DEFAULT_GATES: Gates = {
  legMinSharpe: 1.2,
  requiredYears: [2022, 2023],
  yearMinSharpe: 0,
  minLegs: 2,
  weights: [1.5, 0.5],
  comboMinSharpe: 1.7,
  comboMinRecentSharpe: 1.8,
  subUniverseMargin: 0.1,
}
