import { type MutableRefObject, useEffect, useRef } from "react";

/**
 * The hold-to-talk mic. Press and hold (or Spacebar, wired by the parent) to
 * record; the parent's global pointerup ends the turn even if the pointer has
 * dragged off the button. While recording, a canvas waveform is driven off the
 * live mic AnalyserNode so the visual is the exact audio being captured.
 */
export function MicButton({
  recording,
  disabled,
  analyserRef,
  onHold,
}: {
  recording: boolean;
  disabled: boolean;
  analyserRef: MutableRefObject<AnalyserNode | null>;
  onHold: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!recording) return;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    let raf = 0;
    const draw = () => {
      const analyser = analyserRef.current;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (analyser) {
        const bins = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(bins);
        const bars = 30;
        const cx = canvas.width / 2;
        const cy = canvas.height / 2;
        const color = getComputedStyle(document.documentElement)
          .getPropertyValue("--pink")
          .trim();
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.85;
        for (let k = 0; k < bars; k += 1) {
          const v = bins[Math.floor((k / bars) * bins.length)] / 255;
          const h = 6 + v * 52;
          const x = cx + (k - bars / 2) * 9;
          ctx.beginPath();
          ctx.roundRect(x - 2.5, cy - h / 2, 5, h, 2.5);
          ctx.fill();
        }
      }
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [recording, analyserRef]);

  return (
    <div className="relative grid place-items-center">
      <canvas
        ref={canvasRef}
        width={320}
        height={140}
        className="pointer-events-none absolute z-0 h-[70px] w-[160px]"
      />
      <button
        type="button"
        disabled={disabled}
        aria-pressed={recording}
        aria-label={recording ? "Aufnahme laeuft" : "Halten zum Sprechen"}
        onPointerDown={(e) => {
          e.preventDefault();
          if (!disabled) onHold();
        }}
        className={[
          "relative z-10 grid size-24 place-items-center rounded-full text-3xl text-white",
          "shadow-clay transition-[transform,box-shadow] duration-100 select-none touch-none cursor-pointer",
          "enabled:hover:-translate-y-1 enabled:hover:brightness-105",
          "enabled:active:translate-y-1 enabled:active:shadow-clay-in enabled:active:brightness-100",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          "focus-visible:outline-4 focus-visible:outline-offset-4 focus-visible:outline-brand",
          recording
            ? "bg-gradient-to-br from-pink to-again animate-pulse"
            : "bg-gradient-to-br from-brand to-pink",
        ].join(" ")}
      >
        {recording ? "●" : "🎙️"}
      </button>
    </div>
  );
}
