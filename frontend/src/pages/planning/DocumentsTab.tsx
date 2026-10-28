import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { DragEvent, FormEvent } from "react";

import { api, errorMessage } from "../../api/client";
import type { PlanningDocument, PlanningDocumentDetail } from "../../api/types";
import { Icon } from "../../components/Icon";
import type { IconName } from "../../components/Icon";
import {
  Alert,
  Card,
  ConfirmButton,
  EmptyState,
  ErrorState,
  Field,
  Loading,
  formatBytes,
  timeAgo,
} from "../../components/ui";
import { CATEGORY_LABEL, MAX_UPLOAD_BYTES, orgParam, planningKey } from "./shared";

const ACCEPT = ".csv,.tsv,.xlsx,.xlsm,.md,.markdown,.txt,.json,.pdf,.docx,.png,.jpg,.jpeg";

const FORMAT: Record<string, { label: string; icon: IconName; tone: string }> = {
  csv: { label: "Spreadsheet (CSV)", icon: "sheet", tone: "sheet" },
  xlsx: { label: "Excel workbook", icon: "sheet", tone: "sheet" },
  markdown: { label: "Markdown", icon: "file", tone: "text" },
  text: { label: "Text", icon: "file", tone: "text" },
  json: { label: "JSON", icon: "file", tone: "text" },
  note: { label: "Note", icon: "note", tone: "note" },
  binary: { label: "File", icon: "file", tone: "" },
};

type Notice = { kind: "success" | "error"; text: string } | null;

function Preview({ doc, orgId }: { doc: PlanningDocument; orgId: number | null }) {
  const [open, setOpen] = useState(false);
  const a = doc.analysis;
  const hasTable = a.columns.length > 0;
  const detail = useQuery({
    queryKey: planningKey(orgId, "document", String(doc.id)),
    queryFn: () =>
      api<PlanningDocumentDetail>(`/api/planning/documents/${doc.id}${orgParam(orgId)}`),
    enabled: open && !hasTable,
  });
  if (a.format === "binary") return null;
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{hasTable ? `Preview table (${a.row_count} rows)` : "Preview text"}</summary>
      {hasTable ? (
        <div className="table-wrap" role="region" aria-label={`${doc.title} preview`} tabIndex={0}>
          <table>
            <thead>
              <tr>
                {a.columns.map((c, i) => (
                  <th key={i} scope="col">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {a.rows.map((row, i) => (
                <tr key={i}>
                  {a.columns.map((_, j) => (
                    <td key={j}>{row[j] ?? ""}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : detail.isPending ? (
        <Loading />
      ) : detail.isError ? (
        <ErrorState error={detail.error} />
      ) : (
        <pre>{detail.data.text}</pre>
      )}
    </details>
  );
}

function DocumentItem({ doc, orgId }: { doc: PlanningDocument; orgId: number | null }) {
  const qc = useQueryClient();
  const a = doc.analysis;
  const format = FORMAT[a.format] ?? FORMAT.binary!;
  const remove = useMutation({
    mutationFn: () =>
      api(`/api/planning/documents/${doc.id}${orgParam(orgId)}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: planningKey(orgId) }),
  });
  const shown = a.needs.slice(0, 8);
  return (
    <li className="doc">
      <div className="doc-head">
        <span className={`doc-icon ${format.tone}`} aria-hidden="true">
          <Icon name={format.icon} />
        </span>
        <div className="doc-text">
          <div className="doc-title">{doc.title}</div>
          <div className="doc-meta">
            {format.label} · {formatBytes(doc.size_bytes)}
            {doc.created_by_name ? ` · ${doc.created_by_name}` : ""} · {timeAgo(doc.created_at)}
          </div>
        </div>
        <div className="actions">
          {doc.kind === "upload" && (
            <a
              className="icon-btn"
              href={`/api/planning/documents/${doc.id}/download${orgParam(orgId)}`}
              aria-label={`Download ${doc.title}`}
              title="Download"
            >
              <Icon name="download" />
            </a>
          )}
          <ConfirmButton
            label="Delete"
            confirmLabel="Delete"
            prompt={`Delete ${doc.title}?`}
            className="btn btn-ghost btn-sm"
            busy={remove.isPending}
            onConfirm={() => remove.mutate()}
          />
        </div>
      </div>
      {a.needs.length > 0 ? (
        <div className="chips" aria-label="Needs found">
          {shown.map((n, i) => (
            <span key={i} className="chip" title={CATEGORY_LABEL[n.category]}>
              {n.item}
              {n.quantity !== null && <strong>{n.quantity}</strong>}
            </span>
          ))}
          {a.needs.length > shown.length && (
            <span className="chip">+{a.needs.length - shown.length} more</span>
          )}
        </div>
      ) : (
        a.format !== "binary" && (
          <p className="doc-warn muted">No item quantities found; mentions still count.</p>
        )
      )}
      {a.dietary.length > 0 && (
        <div className="chips">
          {a.dietary.map((d) => (
            <span key={d} className="chip">
              {d}
            </span>
          ))}
        </div>
      )}
      {a.warnings.map((w) => (
        <p key={w} className="doc-warn">
          {w}
        </p>
      ))}
      {remove.isError && <Alert kind="error">{errorMessage(remove.error)}</Alert>}
      <Preview doc={doc} orgId={orgId} />
    </li>
  );
}

export function DocumentsTab({ orgId }: { orgId: number | null }) {
  const qc = useQueryClient();
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");

  const docs = useQuery({
    queryKey: planningKey(orgId, "documents"),
    queryFn: () => api<PlanningDocument[]>(`/api/planning/documents${orgParam(orgId)}`),
  });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const results: string[] = [];
      for (const file of files) {
        if (file.size > MAX_UPLOAD_BYTES) {
          throw new Error(`${file.name} is larger than ${formatBytes(MAX_UPLOAD_BYTES)}.`);
        }
        const doc = await api<PlanningDocument>(
          `/api/planning/documents?filename=${encodeURIComponent(file.name)}${orgParam(orgId, "&")}`,
          { method: "POST", rawBody: file },
        );
        const n = doc.analysis.needs.length;
        results.push(`${doc.title}${n ? ` (${n} need${n === 1 ? "" : "s"} found)` : ""}`);
      }
      return results;
    },
    onSuccess: (names) => setNotice({ kind: "success", text: `Uploaded ${names.join(", ")}.` }),
    onError: (err) =>
      setNotice({ kind: "error", text: err instanceof Error ? errorMessage(err) : String(err) }),
    onSettled: () => qc.invalidateQueries({ queryKey: planningKey(orgId) }),
  });

  const note = useMutation({
    mutationFn: () =>
      api<PlanningDocument>(`/api/planning/notes${orgParam(orgId)}`, {
        method: "POST",
        body: { title, body },
      }),
    onSuccess: (doc) => {
      const n = doc.analysis.needs.length;
      setNotice({
        kind: "success",
        text: `Saved “${doc.title}”${n ? `: ${n} need${n === 1 ? "" : "s"} found` : ""}.`,
      });
      setTitle("");
      setBody("");
      void qc.invalidateQueries({ queryKey: planningKey(orgId) });
    },
    onError: (err) => setNotice({ kind: "error", text: errorMessage(err) }),
  });

  const pick = (list: FileList | null) => {
    if (list && list.length > 0) {
      setNotice(null);
      upload.mutate(Array.from(list));
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files);
  };
  const saveNote = (e: FormEvent) => {
    e.preventDefault();
    setNotice(null);
    note.mutate();
  };

  return (
    <div className="grid-2">
      <div>
        <Card
          title="Upload files"
          description="Spreadsheets and notes are read for items, quantities and categories."
        >
          <label
            className={dragging ? "dropzone dragging" : "dropzone"}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <span className="empty-icon">
              <Icon name="upload" size={22} />
            </span>
            <strong>{upload.isPending ? "Uploading…" : "Drop files here, or browse"}</strong>
            <small>CSV, Excel, Markdown, text or JSON are analyzed; other files are stored.</small>
            <small>Up to {formatBytes(MAX_UPLOAD_BYTES)} each.</small>
            <input
              type="file"
              className="sr-only"
              multiple
              accept={ACCEPT}
              aria-label="Upload planning files"
              disabled={upload.isPending}
              onChange={(e) => {
                pick(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </Card>
        <Card
          title="Write a note"
          description="Survey results, gaps or anything the planner should know."
        >
          <form onSubmit={saveNote} className="stack" aria-label="New planning note">
            <Field label="Title">
              {(id) => (
                <input
                  id={id}
                  required
                  maxLength={200}
                  placeholder="e.g. Intake survey: November"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              )}
            </Field>
            <Field
              label="Note"
              hint="Lines like “Infant formula: 30 cases” become needs the planner sizes requests from."
            >
              {(id, hint) => (
                <textarea
                  id={id}
                  aria-describedby={hint}
                  required
                  rows={6}
                  maxLength={20000}
                  placeholder={
                    "Biggest gaps this month:\n- Infant formula: 30 cases\n- Rice: 40 bags"
                  }
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
              )}
            </Field>
            <div className="actions">
              <button type="submit" className="btn btn-primary" disabled={note.isPending}>
                {note.isPending ? "Saving…" : "Save note"}
              </button>
            </div>
          </form>
        </Card>
      </div>
      <Card
        title="Documents and notes"
        description="Everything here feeds the sample plans. Newest first."
      >
        {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
        {docs.isPending ? (
          <Loading />
        ) : docs.isError ? (
          <ErrorState error={docs.error} onRetry={() => docs.refetch()} />
        ) : docs.data.length === 0 ? (
          <EmptyState icon={<Icon name="file" size={24} />} title="Nothing here yet">
            Upload a needs spreadsheet or write a note, and the planner will use it.
          </EmptyState>
        ) : (
          <ul className="doc-list">
            {docs.data.map((d) => (
              <DocumentItem key={d.id} doc={d} orgId={orgId} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
