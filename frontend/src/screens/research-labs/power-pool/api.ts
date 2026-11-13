/** LLM Alpha Lab: datasets or fields, a model, cores and simulations, then add the task to Tasks. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'
import type { FieldFilterState } from '@/screens/data/state'

type Schemas = components['schemas']

export type PowerPoolOptions = Schemas['PowerPoolOptions']
export type PowerPoolPreview = Schemas['PowerPoolPreview']

export interface PowerPoolRequest {
  region: string
  delay: number
  universe: string
  dataset_ids: string[]
  /** The Data Explorer's filter the datasets were chosen under; `null` uses every field. */
  field_filter?: FieldFilterState | null
  model: string | null
  /** Empty keeps every neutralization BRAIN offers for the market. */
  neutralizations: string[]
  /** Empty draws from every downloaded universe of the market. */
  universes: string[]
  cores: number
  simulations: number
  /** `power_pool` (few operators and fields) or `regular` (a full-size Alpha with its reason). */
  mode: AlphaMode
  /** Fields ticked by hand; empty means every field of the chosen datasets. */
  field_ids: string[]
  /** Regular only: the fewest different operators, the most in all, the most data fields. */
  min_operators: number
  max_operators: number
  max_fields: number
  /** What the LLM should explore, in the researcher's words. */
  focus: string
  /** Hold every written Alpha until it is approved. */
  review: boolean
}

export type AlphaMode = 'power_pool' | 'regular'

/** An Alpha the LLM wrote and a task is holding for the researcher's decision. */
export interface ReviewItem {
  trialId: number
  taskId: number
  task: string
  mode: string
  dataset: string
  expression: string
  hypothesis: string
  operators: number
  universe: string | null
  neutralization: string | null
  decay: number | null
}

const B = '/api/power-pool-lab'

export const powerPoolLab = {
  options: () => http.get<PowerPoolOptions>(`${B}/options`),
  /** Free: no LLM call, no simulation. */
  preview: (body: PowerPoolRequest) => http.post<PowerPoolPreview>(`${B}/preview`, body),
  addTask: (body: PowerPoolRequest) => http.post<Schemas['AddedTask']>(`${B}/tasks`, body),
  review: () => http.get<{ items: ReviewItem[] }>(`${B}/review`),
  decide: (approve: number[], reject: number[]) =>
    http.post<{ approved: number; rejected: number }>(`${B}/review`, { approve, reject }),
}
