import { useEffect, useState } from "react";
import { api, type Feature } from "./api";
import LoadingDock from "./views/LoadingDock";
import Convert from "./views/Convert";
import EditValues from "./views/EditValues";
import MatchFields from "./views/MatchFields";

// The stages of the pipeline, always in view so the audience sees where in the workflow we are.
const STAGES = ["Loading dock", "Match fields", "Edit values", "Convert"];

// Where you are survives a reload of the tab (not a new tab): a refresh mid-rehearsal
// shouldn't send you back to an empty loading dock.
function remembered<T>(key: string, fallback: T): T {
  try {
    const v = sessionStorage.getItem(`wrangle.${key}`);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}
function remember(key: string, value: unknown) {
  try {
    sessionStorage.setItem(`wrangle.${key}`, JSON.stringify(value));
  } catch {
    /* storage blocked: nothing to remember with */
  }
}

export default function App() {
  const [features, setFeatures] = useState<Feature[]>([]);
  const [feature, setFeature] = useState(() => remembered("feature", "roads"));
  const [stage, setStage] = useState(() => remembered("stage", 0));
  // What each feature type has queued for ingest; Match fields works on it.
  const [queues, setQueues] = useState<Record<string, string[]>>(() => remembered("queues", {}));
  useEffect(() => remember("feature", feature), [feature]);
  useEffect(() => remember("stage", stage), [stage]);
  useEffect(() => remember("queues", queues), [queues]);
  const queue = queues[feature] ?? [];
  const setQueue = (fn: (q: string[]) => string[]) => setQueues((all) => ({ ...all, [feature]: fn(all[feature] ?? []) }));

  useEffect(() => {
    // Talk order: roads are the main thread, hydrants the speed run.
    const rank = (f: Feature) => (f.feature === "roads" ? 0 : 1);
    api
      .features()
      .then((fs) => setFeatures([...fs].sort((a, b) => rank(a) - rank(b))))
      .catch(console.error);
  }, []);

  const current = features.find((f) => f.feature === feature);

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center gap-5 border-b border-line bg-panel px-5 py-3">
        <div className="text-xl font-semibold tracking-tight">wrangle</div>

        <div className="flex rounded-lg border border-line p-0.5">
          {features.map((f) => (
            <button
              key={f.feature}
              onClick={() => setFeature(f.feature)}
              className={`rounded-md px-3 py-1 ${f.feature === feature ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}
            >
              {f.title}
            </button>
          ))}
        </div>

        <nav className="ml-auto flex items-center gap-1">
          {STAGES.map((label, i) => (
            <div key={label} className="flex items-center">
              {i > 0 && <span className="px-1 text-ink-3">→</span>}
              <button
                onClick={() => setStage(i)}
                className={`flex items-center gap-2 rounded-md px-3 py-1.5 ${i === stage ? "bg-ink text-surface" : "text-ink-2 hover:bg-raised"}`}
              >
                <span className="tabular text-sm opacity-70">{i + 1}</span>
                {label}
              </button>
            </div>
          ))}
        </nav>
      </header>

      <main className="min-h-0 flex-1">
        {/* The dock stays mounted while matching, so its map and layers are waiting on the way back. */}
        {current && (
          <div className={stage === 0 ? "h-full" : "hidden"}>
            <LoadingDock key={feature} feature={current} queue={queue} setQueue={setQueue} onNext={() => setStage(1)} />
          </div>
        )}
        {current && stage === 1 && <MatchFields key={feature} feature={current} queue={queue} onBack={() => setStage(0)} onNext={() => setStage(2)} />}
        {current && stage === 2 && <EditValues key={feature} feature={current} queue={queue} onBack={() => setStage(1)} onNext={() => setStage(3)} />}
        {current && stage === 3 && <Convert key={feature} feature={current} queue={queue} onBack={() => setStage(2)} />}
      </main>
    </div>
  );
}
