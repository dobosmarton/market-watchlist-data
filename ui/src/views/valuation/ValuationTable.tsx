import { useMemo, useState } from "react";
import type { Company } from "./schema";
import { COLUMNS, cell, nextSort, type Sort, sortCompanies } from "./table";

export function ValuationTable({ companies }: { companies: Company[] }) {
  const [sort, setSort] = useState<Sort>({ key: "name", ascending: true });
  const sorted = useMemo(() => sortCompanies(companies, sort), [companies, sort]);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th
                key={column.key}
                aria-sort={
                  column.key === sort.key
                    ? sort.ascending
                      ? "ascending"
                      : "descending"
                    : undefined
                }
              >
                <button
                  type="button"
                  className="sort"
                  onClick={() => setSort(nextSort(sort, column.key))}
                >
                  {column.title}
                  {column.key === sort.key && (sort.ascending ? " ▲" : " ▼")}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((company) => (
            <tr key={company.slug}>
              {COLUMNS.map((column) => (
                <td key={column.key}>{cell(company, column)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
