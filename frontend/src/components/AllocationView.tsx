import type { Allocation } from "../api/types";
import { Empty, Progress, TableRegion } from "./ui";

export function AllocationView({
  allocation,
  showOrganizations,
}: {
  allocation: Allocation;
  showOrganizations: boolean;
}) {
  if (allocation.lots.length === 0) return <Empty>This round had no inventory.</Empty>;
  return (
    <div className="stack">
      {allocation.lots.map((lot) => (
        <article key={lot.lot_id} className="card lot-result" aria-labelledby={`lot-${lot.lot_id}`}>
          <div className="lot-result-head">
            <h3 id={`lot-${lot.lot_id}`}>{lot.name}</h3>
            <dl className="mini-stats">
              <div>
                <dt>Supply</dt>
                <dd>
                  {lot.supply} {lot.unit}
                </dd>
              </div>
              <div>
                <dt>Allocated</dt>
                <dd>
                  {lot.allocated_total} {lot.unit}
                </dd>
              </div>
              <div>
                <dt>Returned to stock</dt>
                <dd>
                  {lot.unallocated} {lot.unit}
                </dd>
              </div>
            </dl>
          </div>
          <Progress
            value={lot.allocated_total}
            max={lot.supply}
            label={`${lot.name}: ${lot.allocated_total} of ${lot.supply} ${lot.unit} allocated`}
          />
          {lot.lines.length === 0 ? (
            <Empty>
              {showOrganizations
                ? "No requests were made for this lot."
                : "Your organization did not request this lot."}
            </Empty>
          ) : (
            <TableRegion label={`Allocation for ${lot.name}`}>
              <table className="results">
                <thead>
                  <tr>
                    {showOrganizations && <th scope="col">Organization</th>}
                    <th scope="col" className="num">
                      Requested
                    </th>
                    <th scope="col">Allocated</th>
                    <th scope="col">How this was decided</th>
                  </tr>
                </thead>
                <tbody>
                  {lot.lines.map((line) => (
                    <tr key={line.request_id} data-testid={`result-${line.request_id}`}>
                      {showOrganizations && <td className="row-title">{line.organization_name}</td>}
                      <td className="num">
                        {line.requested} {lot.unit}
                      </td>
                      <td className="alloc-cell">
                        <strong>
                          {line.allocated} {lot.unit}
                        </strong>
                        <Progress
                          value={line.allocated}
                          max={line.requested}
                          label={`${line.allocated} of ${line.requested} ${lot.unit} filled`}
                        />
                      </td>
                      <td className="explanation">{line.explanation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableRegion>
          )}
        </article>
      ))}
    </div>
  );
}
