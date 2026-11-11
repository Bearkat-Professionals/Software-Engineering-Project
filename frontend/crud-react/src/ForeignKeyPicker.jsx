import { useEffect, useRef, useState } from "react";
import { api } from "./api";

export default function ForeignKeyPicker({
  column,
  parentTable,
  parentKey,
  labelColumn,
  value,
  optional,
  onChange,
}) {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const typingRef = useRef(false);

  useEffect(() => {
    if (value === "" || value == null) {
      if (typingRef.current) typingRef.current = false;
      else setQuery("");
      return;
    }

    let ignore = false;
    api("/api/" + parentTable + "/" + value)
      .then((row) => {
        if (!ignore) setQuery(String(row[labelColumn] ?? value));
      })
      .catch((err) => {
        if (!ignore) setError(err.message);
      });
    return () => {
      ignore = true;
    };
  }, [labelColumn, parentTable, value]);

  useEffect(() => {
    let ignore = false;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ limit: "20" });
      const search = query.trim();
      if (search) {
        params.set("search", search);
        params.set("search_column", labelColumn);
      }

      setLoading(true);
      setError("");
      api("/api/" + parentTable + "?" + params.toString())
        .then((rows) => {
          if (!ignore) setMatches(rows);
        })
        .catch((err) => {
          if (!ignore) setError(err.message);
        })
        .finally(() => {
          if (!ignore) setLoading(false);
        });
    }, 300);

    return () => {
      ignore = true;
      window.clearTimeout(timer);
    };
  }, [labelColumn, parentTable, query]);

  function typeSearch(event) {
    typingRef.current = true;
    setQuery(event.target.value);
    setOpen(true);
    onChange("");
  }

  function choose(row) {
    typingRef.current = false;
    setQuery(String(row[labelColumn] ?? row[parentKey]));
    setOpen(false);
    onChange(String(row[parentKey]));
  }

  function clearChoice() {
    typingRef.current = false;
    setQuery("");
    setOpen(false);
    onChange("");
  }

  return (
    <label className="fk-field">
      {column}
      <div className="fk-picker">
        <input
          value={query}
          onChange={typeSearch}
          onFocus={() => setOpen(true)}
          role="combobox"
          aria-expanded={open}
          aria-controls={column + "-matches"}
          aria-autocomplete="list"
        />
        {open && (
          <div className="fk-matches" id={column + "-matches"} role="listbox">
            {optional && (
              <button type="button" className="fk-match" onClick={clearChoice}>
                default / none
              </button>
            )}
            {loading && <span className="fk-note">Searching...</span>}
            {error && <span className="fk-error" role="alert">{error}</span>}
            {!loading && !error && matches.length === 0 && (
              <span className="fk-note">No matches.</span>
            )}
            {!loading && !error && matches.map((row) => {
              const id = String(row[parentKey]);
              const selected = String(value) === id;
              return (
                <button
                  type="button"
                  key={id}
                  className={"fk-match" + (selected ? " selected" : "")}
                  onClick={() => choose(row)}
                  role="option"
                  aria-selected={selected}
                >
                  {id} – {row[labelColumn]}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </label>
  );
}
