export type Options<T> = readonly (readonly [value: T, label: string])[];

/** A row of toggle buttons, one of them pressed. */
export function Segmented<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: Options<T>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="segmented" aria-label={label}>
      {options.map(([option, text]) => (
        <button
          key={option}
          type="button"
          aria-pressed={option === value}
          onClick={() => onChange(option)}
        >
          {text}
        </button>
      ))}
    </fieldset>
  );
}
