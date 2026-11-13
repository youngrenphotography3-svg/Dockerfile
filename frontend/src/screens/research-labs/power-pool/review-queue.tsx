/** Alphas the LLM wrote for a review-on task: read the reason, approve or reject. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { powerPoolLab } from '@/screens/research-labs/power-pool/api'
import { Button, Checkbox, ErrorNotice, Panel } from '@/ui/kit'

export function ReviewQueue() {
  const queryClient = useQueryClient()
  const [ticked, setTicked] = useState<number[]>([])
  const list = useQuery({
    queryKey: ['power-pool-lab', 'review'],
    queryFn: powerPoolLab.review,
    refetchInterval: 5000,
  })
  const items = list.data?.items ?? []
  const decide = useMutation({
    mutationFn: ({ approve, reject }: { approve: number[]; reject: number[] }) =>
      powerPoolLab.decide(approve, reject),
    onSuccess: (done) => {
      setTicked([])
      toast.success(`Approved ${done.approved} · rejected ${done.rejected}`)
      void queryClient.invalidateQueries({ queryKey: ['power-pool-lab', 'review'] })
      void queryClient.invalidateQueries({ queryKey: ['lab-tasks'] })
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  if (!list.isLoading && items.length === 0 && !list.isError) return null
  const all = items.map((i) => i.trialId)
  const pick = ticked.length > 0 ? ticked : []

  return (
    <Panel title={`Review · ${items.length} waiting`}>
      <div className="flex flex-col gap-3">
        <p className="text-body-compact text-pretty text-ink-subtle">
          These Alphas were written by the LLM for a task with Review on. Nothing here has been
          simulated. Read the reason first: if you cannot say why it should work, reject it, and the
          LLM is told it was thrown away.
        </p>
        {list.isError && <ErrorNotice error={list.error} title="Could not load the review list" />}
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="primary"
            disabled={pick.length === 0 || decide.isPending}
            onClick={() => decide.mutate({ approve: pick, reject: [] })}
          >
            Approve {pick.length || ''} ticked
          </Button>
          <Button
            size="sm"
            disabled={pick.length === 0 || decide.isPending}
            onClick={() => decide.mutate({ approve: [], reject: pick })}
          >
            Reject {pick.length || ''} ticked
          </Button>
          <Button size="sm" onClick={() => setTicked(ticked.length === all.length ? [] : all)}>
            {ticked.length === all.length ? 'Untick all' : 'Tick all'}
          </Button>
        </div>
        <ul className="max-h-[32rem] divide-y divide-hairline overflow-auto rounded-md border border-hairline">
          {items.map((i) => (
            <li key={i.trialId} className="flex flex-col gap-1.5 px-3 py-3">
              <Checkbox
                aria-label={`Select Alpha ${i.trialId}`}
                label={
                  <span className="text-body-compact text-ink-subtle">
                    {i.task} · {i.dataset} · {i.operators} different operators
                    {i.universe ? ` · ${i.universe}` : ''}
                    {i.neutralization ? ` · ${i.neutralization}` : ''}
                    {i.decay != null ? ` · decay ${i.decay}` : ''}
                  </span>
                }
                checked={ticked.includes(i.trialId)}
                onChange={(on) =>
                  setTicked(on ? [...ticked, i.trialId] : ticked.filter((x) => x !== i.trialId))
                }
              />
              {i.hypothesis && (
                <p className="text-body-compact text-pretty text-ink">{i.hypothesis}</p>
              )}
              <pre className="num overflow-auto rounded-md bg-canvas p-2 text-caption whitespace-pre-wrap text-ink-muted">
                {i.expression}
              </pre>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  )
}
