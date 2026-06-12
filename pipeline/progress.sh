# Drive WebODM's task progress bar.
#
# NodeODM listens on UDP :6367 for ODM's progress datagrams
# (libs/ProgressReceiver.js): "PGUP/<pid>/<task-uuid>/<percent>". ODM sends these
# after each stage; without them the WebODM task shows only a spinning
# "Processing" with an empty bar. Sourced by run.sh and the pipeline scripts;
# run.sh exports EFFIGIES_TASK_UUID (= the NodeODM project/task name).
#
# Best-effort by design: progress must never fail a run (|| true), and outside
# NodeODM (no uuid set) it is a no-op.
progress() {
  [[ -n "${EFFIGIES_TASK_UUID:-}" ]] || return 0
  { echo -n "PGUP/$$/${EFFIGIES_TASK_UUID}/$1" > /dev/udp/127.0.0.1/6367; } 2>/dev/null || true
}
