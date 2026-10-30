import { useEffect, useState } from "react";
import { api } from "./api";
import { BOOLEANS, ENUMS, FOREIGN_KEYS, OPTIONAL_FK } from "./fields";
import "./App.css";

const PREFERRED = ["vendor", "device_type", "device"];

function orderedNames(tables) {

  const names = Object.keys(tables);
  
  return [

    ...PREFERRED.filter((name) => names.includes(name)),
    ...names.filter((name) => !PREFERRED.includes(name)),

  ];

}

function blankValues(tableName, table) {

  const values = {};

  for (const column of table.columns) {

    if (column === table.primary_key) continue;

    const choices = (ENUMS[tableName] || {})[column];
    const isBool = (BOOLEANS[tableName] || []).includes(column);

    if (isBool) values[column] = column === "enabled";

    else if (choices) values[column] = choices[0];

    else values[column] = "";

  }

  return values;

}

function valuesFromRow(tableName, table, row) {

  const values = blankValues(tableName, table);

  for (const column of Object.keys(values)) {

    const isBool = (BOOLEANS[tableName] || []).includes(column);

    if (isBool) values[column] = row[column] === true;

    else if (row[column] == null) values[column] = "";

    else values[column] = String(row[column]);

  }

  return values;

}

export default function App() {

  const [tables, setTables] = useState({});
  const [tableName, setTableName] = useState("");
  const [rows, setRows] = useState([]);
  const [editingId, setEditingId] = useState(null);
  const [formValues, setFormValues] = useState({});
  const [lookups, setLookups] = useState({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const table = tables[tableName];

  useEffect(() => {

    api("/api/tables").then((data) => {

      setTables(data);
      setTableName(data.vendor ? "vendor" : Object.keys(data)[0]);
    })

    .catch((err) => setError(err.message));

  }, []);

  useEffect(() => {

    if (!tableName || !tables[tableName]) return;

    let ignore = false;

    setEditingId(null);
    setMessage("");
    setError("");
    setFormValues(blankValues(tableName, tables[tableName]));

    const parentNames = [...new Set(Object.values(FOREIGN_KEYS[tableName] || {}).map((spec) => spec.table))];

    Promise.all([

      api("/api/" + tableName),
      ...parentNames.map(async (name) => [name, await api("/api/" + name)]),

    ])

    .then(([rowData, ...parents]) => {

      if (ignore) return;

      setRows(rowData);
      const nextLookups = {};

      for (const [name, parentRows] of parents) nextLookups[name] = parentRows;

      setLookups(nextLookups);

    })

    .catch((err) => {

      if (!ignore) setError(err.message);

    });
    return () => {

      ignore = true;

    };

  }, [tableName, tables]);

  function setField(column, value) {

    setFormValues((current) => ({ ...current, [column]: value }));

  }

  function bodyFromForm() {

    const body = {};

    for (const column of table.columns) {

      if (column === table.primary_key) continue;

      const isBool = (BOOLEANS[tableName] || []).includes(column);
      const value = formValues[column];

      if (isBool) body[column] = value === true;

      else if (column.endsWith("_id")) body[column] = value === "" || value == null ? null : Number(value);

      else body[column] = value == null || String(value).trim() === "" ? null : String(value).trim();

    }

    return body;

  }

  async function reloadRows() {

    setRows(await api("/api/" + tableName));

  }

  async function onSubmit(event) {

    event.preventDefault();

    try {

      const body = bodyFromForm();

      if (editingId == null) {

        const created = await api("/api/" + tableName, { method: "POST", body });

        setMessage("Added " + tableName + " " + created[table.primary_key] + ".");

      } else {

        await api("/api/" + tableName + "/" + editingId, { method: "PUT", body });

        setMessage("Saved " + tableName + " " + editingId + ".");
        setEditingId(null);

      }

      setError("");
      setFormValues(blankValues(tableName, table));

      await reloadRows();

    } catch (err) {

      setError(err.message);
      setMessage("");

    }

  }

  async function onEdit(id) {

    try {

      const row = await api("/api/" + tableName + "/" + id);

      setEditingId(row[table.primary_key]);
      setFormValues(valuesFromRow(tableName, table, row));
      setError("");

    } catch (err) {

      setError(err.message);

    }

  }

  async function onDelete(id) {

    if (!window.confirm("Delete " + tableName + " " + id + "?")) return;

    try {

      await api("/api/" + tableName + "/" + id, { method: "DELETE" });

      if (String(editingId) === String(id)) {

        setEditingId(null);
        setFormValues(blankValues(tableName, table));

      }

      setError("");
      setMessage("Deleted " + tableName + " " + id + ".");

      await reloadRows();

    } catch (err) {

      setError(err.message);
      setMessage("");

    }
  }

  function cancelEdit() {

    setEditingId(null);
    setFormValues(blankValues(tableName, table));

  }

  if (!table) {

    return <p className={error ? "error" : ""}>{error || "Loading tables..."}</p>;

  }

  const columns = table.columns.filter((column) => column !== table.primary_key);

  return (

    <>

      <h1>CRUD Test</h1>

      <p>

        This is a test of the CRUD functionality to test the database.
        This is also using the React framework to create the interface.

      </p>

      <label>

        Table:

        <select value={tableName} onChange={(event) => setTableName(event.target.value)}>

          {orderedNames(tables).map((name) => (

            <option key={name} value={name}>{name}</option>

          ))}

        </select>

      </label>

      {message && <p className="message" role="status">{message}</p>}
      {error && <p className="error" role="alert">{error}</p>}

      <h2> {editingId == null ? "Add " + tableName : "Edit " + tableName} </h2>

      <form onSubmit={onSubmit} autoComplete="off">

        {columns.map((column) => {

          const choices = (ENUMS[tableName] || {})[column];
          const fk = (FOREIGN_KEYS[tableName] || {})[column];
          const isBool = (BOOLEANS[tableName] || []).includes(column);

          if (isBool) {

            return (

              <label key={column}>

                {column}

                <input type="checkbox" checked={formValues[column] === true} onChange={(event) => setField(column, event.target.checked)} />

              </label>

            );

          }

          if (choices) {

            return (

              <label key={column}>

                {column}

                <select value={formValues[column] ?? ""} onChange={(event) => setField(column, event.target.value)}>

                  {choices.map((item) => <option key={item} value={item}>{item}</option>)}

                </select>

              </label>

            );

          }

          if (fk) {

            const optional = (OPTIONAL_FK[tableName] || []).includes(column);
            const parentKey = tables[fk.table].primary_key;
            const parents = lookups[fk.table] || [];

            return (

              <label key={column}>

                {column}

                <select value={formValues[column] ?? ""} onChange={(event) => setField(column, event.target.value)}>

                  <option value="">{optional ? "default / none" : "Select..."}</option>

                  {parents.map((parent) => (

                    <option key={parent[parentKey]} value={String(parent[parentKey])}>

                      {parent[parentKey]} - {parent[fk.label]}

                    </option>

                  ))}

                </select>

              </label>

            );

          }

          return (

            <label key={column}>

              {column}

              <input value={formValues[column] ?? ""} onChange={(event) => setField(column, event.target.value)} />

            </label>

          );

        })}

        <button type="submit">{editingId == null ? "Add Row" : "Save Changes"}</button>

        {editingId != null && (

          <button type="button" className="secondary" onClick={cancelEdit}>cancel</button>
        )}

      </form>

      <h2>Rows</h2>

      <div className="rows">

        {rows.length === 0 ? <p>No rows in {tableName}.</p> : (

          <table>

            <thead>

              <tr>

                {table.columns.map((column) => <th key={column}>{column}</th>)}

                <th>Actions</th>

              </tr>

            </thead>

            <tbody>

              {rows.map((row) => (

                <tr key={row[table.primary_key]}>

                  {table.columns.map((column) => {

                    const value = row[column];
                    const text = value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);

                    return <td key={column}>{text}</td>;

                  })}

                  <td>

                    <button type="button" className="secondary" onClick={() => onEdit(row[table.primary_key])}>Edit</button>
                    <button type="button" className="danger" onClick={() => onDelete(row[table.primary_key])}>Delete</button>

                  </td>

                </tr>

              ))}

            </tbody>

          </table>

        )}

      </div>

    </>

  );
  
}