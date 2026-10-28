import { useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api/client";
import type { RoundDetail, RoundSummary, VersionInfo } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { backoffDelay, isStale, staleRounds } from "./sync";
import type { KnownVersions, ServerEvent } from "./sync";

export type LiveStatus = "connecting" | "live" | "reconnecting";

interface RealtimeState {
  status: LiveStatus;
  instance: string | null;
  lastSyncAt: number | null;
}

const RealtimeContext = createContext<RealtimeState>({
  status: "connecting",
  instance: null,
  lastSyncAt: null,
});

const DEFAULT_RESYNC_MS = 15_000;

function cachedVersions(qc: QueryClient, roundId: number): KnownVersions | undefined {
  const detail = qc.getQueryData<RoundDetail>(["round", roundId]);
  if (detail) return detail;
  return qc.getQueryData<RoundSummary[]>(["rounds"])?.find((r) => r.id === roundId);
}

function invalidateRound(qc: QueryClient, roundId: number): void {
  void qc.invalidateQueries({ queryKey: ["round", roundId] });
  void qc.invalidateQueries({ queryKey: ["results", roundId] });
  void qc.invalidateQueries({ queryKey: ["preview", roundId] });
  void qc.invalidateQueries({ queryKey: ["rounds"] });
  void qc.invalidateQueries({ queryKey: ["public"] });
}

function invalidateChat(qc: QueryClient, channelId?: number): void {
  void qc.invalidateQueries({ queryKey: ["chat", "channels"] });
  if (channelId !== undefined) {
    void qc.invalidateQueries({ queryKey: ["chat", "messages", channelId] });
  }
}

function invalidateEverything(qc: QueryClient): void {
  void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "me" });
}

/**
 * Keeps cached server state converging with the database:
 *  1. open a WebSocket; on `hello` refetch everything (snapshot after subscribe);
 *  2. on `round.changed` refetch the round if the event's version is newer; on
 *     `chat.*` refetch the conversation list and that conversation;
 *  3. on `resync` refetch everything;
 *  4. every resync interval, compare /api/sync/versions with the cache — this
 *     bounds staleness even if every notification is lost;
 *  5. reconnect with capped, jittered backoff.
 */
export function RealtimeProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { me } = useAuth();
  const userId = me?.user.id ?? null;
  const [state, setState] = useState<RealtimeState>({
    status: "connecting",
    instance: null,
    lastSyncAt: null,
  });
  const resyncMs = useRef(DEFAULT_RESYNC_MS);

  useEffect(() => {
    if (userId === null) return;
    let socket: WebSocket | null = null;
    let attempt = 0;
    let stopped = false;
    let reconnectTimer: number | undefined;

    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${window.location.host}/api/ws`);
      socket.onmessage = (ev) => {
        let msg: ServerEvent & { instance?: string; resync_interval_seconds?: number };
        try {
          msg = JSON.parse(String(ev.data));
        } catch {
          return;
        }
        if (msg.type === "hello") {
          attempt = 0;
          if (msg.resync_interval_seconds) resyncMs.current = msg.resync_interval_seconds * 1000;
          setState((s) => ({ ...s, status: "live", instance: msg.instance ?? null }));
          invalidateEverything(qc);
        } else if (msg.type === "resync") {
          invalidateEverything(qc);
        } else if (msg.scope === "chat") {
          invalidateChat(qc, msg.channel_id);
        } else if (msg.type === "round.changed" && msg.round_id && msg.scope && msg.version) {
          if (isStale(cachedVersions(qc, msg.round_id), msg.scope, msg.version)) {
            invalidateRound(qc, msg.round_id);
          }
        } else if (msg.type === "inventory.changed") {
          void qc.invalidateQueries({ queryKey: ["admin", "lots"] });
        } else if (msg.type === "organizations.changed") {
          void qc.invalidateQueries({ queryKey: ["admin", "orgs"] });
        } else if (msg.type === "users.changed") {
          void qc.invalidateQueries({ queryKey: ["admin", "users"] });
        }
      };
      socket.onclose = (ev) => {
        socket = null;
        if (stopped) return;
        if (ev.code === 4401) {
          // Session ended: /me now returns 401, which signs the user out.
          void qc.invalidateQueries({ queryKey: ["me"] });
          return;
        }
        setState((s) => ({ ...s, status: "reconnecting" }));
        const delay = ev.code === 4409 ? 0 : backoffDelay(attempt++);
        reconnectTimer = window.setTimeout(connect, delay);
      };
    };
    connect();

    // Periodic version check: the recovery bound for missed notifications.
    let syncTimer: number | undefined;
    let chatFingerprint: string | null = null;
    const tick = async () => {
      try {
        const v = await api<{ rounds: VersionInfo[]; chat: string | null }>("/api/sync/versions");
        const stale = staleRounds(v.rounds, (id) => cachedVersions(qc, id));
        for (const id of stale) invalidateRound(qc, id);
        if (chatFingerprint !== null && v.chat !== chatFingerprint) {
          void qc.invalidateQueries({ queryKey: ["chat"] });
        }
        chatFingerprint = v.chat;
        void qc.invalidateQueries({ queryKey: ["admin"], refetchType: "active" });
        setState((s) => ({ ...s, lastSyncAt: Date.now() }));
      } catch {
        // offline or session ended; the next tick or the socket handles it
      }
      if (!stopped) syncTimer = window.setTimeout(tick, resyncMs.current);
    };
    syncTimer = window.setTimeout(tick, resyncMs.current);

    return () => {
      stopped = true;
      window.clearTimeout(reconnectTimer);
      window.clearTimeout(syncTimer);
      socket?.close(1000);
    };
  }, [qc, userId]);

  return <RealtimeContext.Provider value={state}>{children}</RealtimeContext.Provider>;
}

export function useRealtime(): RealtimeState {
  return useContext(RealtimeContext);
}
