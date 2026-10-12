import React, { Component, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { Orb } from "./components/ui/orb";
import { BarVisualizer, useAudioVolume } from "./components/ui/bar-visualizer";

type View = {
  state: string;
  muted: boolean;
  listening: boolean;
  local: MediaStream | null;
  remote: MediaStream | null;
};
const colors: [string, string] = ["#b19acc", "#e2d9ed"];

class VisualBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function useQuietVisuals() {
  const query = "(prefers-reduced-motion: reduce)";
  const [quiet, setQuiet] = useState(
    () => matchMedia(query).matches || document.hidden,
  );
  useEffect(() => {
    const media = matchMedia(query);
    const update = () => setQuiet(media.matches || document.hidden);
    media.addEventListener("change", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      media.removeEventListener("change", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  return quiet;
}

const StaticBars = () => (
  <div className="static-bars">
    {Array.from({ length: 25 }, (_, i) => (
      <i key={i} />
    ))}
  </div>
);
const StaticOrb = () => <div className="static-orb" />;

function OrbView({ view }: { view: View }) {
  const quiet = useQuietVisuals();
  const active = view.state === "running" || view.state === "connecting";
  const volume = useAudioVolume(
    quiet || !active || view.muted ? null : view.local,
  );
  return quiet || !active ? (
    <StaticOrb />
  ) : (
    <Orb
      colors={colors}
      seed={42}
      className="eleven-orb"
      paused={view.state !== "running" && view.state !== "connecting"}
      volumeMode="manual"
      manualInput={volume}
      manualOutput={0}
    />
  );
}

function WaveView({ stream }: { stream: MediaStream | null }) {
  const quiet = useQuietVisuals();
  return !stream || quiet ? (
    <StaticBars />
  ) : (
    <BarVisualizer
      mediaStream={stream}
      barCount={25}
      minHeight={8}
      maxHeight={96}
      centerAlign
      demo={false}
      state="speaking"
      className="signal-bars"
    />
  );
}

/** Views only borrow streams. RTC alone owns microphone acquisition and stop. */
export function mountVisuals(nodes: {
  orb: HTMLElement;
  input: HTMLElement;
  output: HTMLElement;
}) {
  const roots = {
    orb: createRoot(nodes.orb),
    input: createRoot(nodes.input),
    output: createRoot(nodes.output),
  };
  let disposed = false;
  let supportsWebGL = false;
  try {
    const probe = document.createElement("canvas");
    const context = probe.getContext("webgl2");
    supportsWebGL = !!context;
    context?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    /* The static fallback retains all call controls. */
  }
  return {
    update(view: View) {
      if (disposed) return;
      roots.orb.render(
        <VisualBoundary fallback={<StaticOrb />}>
          {supportsWebGL ? <OrbView view={view} /> : <StaticOrb />}
        </VisualBoundary>,
      );
      roots.input.render(
        <VisualBoundary fallback={<StaticBars />}>
          <WaveView stream={view.state === "running" && !view.muted ? view.local : null} />
        </VisualBoundary>,
      );
      roots.output.render(
        <VisualBoundary fallback={<StaticBars />}>
          <WaveView stream={view.state === "running" && view.listening ? view.remote : null} />
        </VisualBoundary>,
      );
    },
    dispose() {
      if (!disposed) {
        disposed = true;
        Object.values(roots).forEach((root) => root.unmount());
      }
    },
  };
}
