// Lists, adds, edits, and deletes rows in every test table.
import { useEffect, useRef, useState } from "react";
import { api, apiPage } from "./api";
import ForeignKeyPicker from "./ForeignKeyPicker";
import { BOOLEANS, ENUMS, FOREIGN_KEYS, OPTIONAL_FK } from "./fields";
import "./App.css";

// Each list request asks for this many rows.
const PAGE_SIZE = 50;
// These tables are listed first because later tables refer to them.
const PREFERRED = ["vendor", "device_type", "device"];

// Put the preferred tables first and keep the rest in the server's order.
function orderedNames(tables) {
  const names = Object.keys(tables);
  return [
    ...PREFERRED.filter((name) => names.includes(name)),
    ...names.filter((name) => !PREFERRED.includes(name)),
  ];
}

// Starting values for an add form. The primary key is assigned by the database.
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

// Copy one row into the form so it can be edited.
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

// Pages are numbered from zero. offset is how many rows to skip.
function pagePath(tableName, page) {
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(page * PAGE_SIZE),
  });
  return "/api/" + tableName + "?" + params.toString();
}

export default function App() {
  const [tables, setTables] = useState({});
  const [tableName, setTableName] = useState("");
  const [page, setPage] = useState(0);
  const [loaded, setLoaded] = useState({ table: "", page: 0, rows: [], total: 0 });
  const [editingId, setEditingId] = useState(null);
  const [formValues, setFormValues] = useState({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  // Refs remember the table and page even after a slow request returns.
  const tableNameRef = useRef("");
  const pageRef = useRef(0);

  const table = tables[tableName];

  useEffect(() => {
    // Load every table's columns, then open vendor when it exists.
    api("/api/tables")
      .then((data) => {
        const initialTable = data.vendor ? "vendor" : Object.keys(data)[0];
        setTables(data);
        tableNameRef.current = initialTable;
        pageRef.current = 0;
        setTableName(initialTable);
        setFormValues(blankValues(initialTable, data[initialTable]));
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!tableName || !tables[tableName]) return;
    let ignore = false;
    // Reload the visible page whenever the table or page changes.
    apiPage(pagePath(tableName, page))
      .then(({ rows, total }) => {
        if (!ignore) setLoaded({ table: tableName, page, rows, total });
      })
      .catch((err) => {
        if (!ignore) setError(err.message);
      });
    return () => {
      ignore = true;
    };
  }, [page, tableName, tables]);

  // Switching tables leaves the edit form and returns to the first page.
  function onTableChange(event) {
    const name = event.target.value;
    tableNameRef.current = name;
    pageRef.current = 0;
    setPage(0);
    setTableName(name);
    setEditingId(null);
    setMessage("");
    setError("");
    setFormValues(blankValues(name, tables[name]));
  }

  function changePage(nextPage) {
    pageRef.current = nextPage;
    setPage(nextPage);
  }

  function setField(column, value) {
    setFormValues((current) => ({ ...current, [column]: value }));
  }

  // Booleans stay true or false. Empty text and empty ids are sent as null.
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

  // Ignore the result if the user has already moved to another table or page.
  async function reloadRows(name = tableName, pageNumber = page) {
    const { rows, total } = await apiPage(pagePath(name, pageNumber));
    if (tableNameRef.current !== name || pageRef.current !== pageNumber) return;
    if (pageNumber > 0 && rows.length === 0 && total <= pageNumber * PAGE_SIZE) {
      changePage(pageNumber - 1);
      return;
    }
    setLoaded({ table: name, page: pageNumber, rows, total });
  }

  // No editing id means add. An editing id replaces that whole row.
  async function onSubmit(event) {
    event.preventDefault();
    const name = tableName;
    const pageNumber = page;
    try {
      const body = bodyFromForm();
      if (editingId == null) {
        const created = await api("/api/" + name, { method: "POST", body });
        if (tableNameRef.current === name) {
          setMessage("Added " + name + " " + created[table.primary_key] + ".");
        }
      } else {
        await api("/api/" + name + "/" + editingId, { method: "PUT", body });
        if (tableNameRef.current === name) {
          setMessage("Saved " + name + " " + editingId + ".");
          setEditingId(null);
        }
      }
      if (tableNameRef.current !== name) return;
      setError("");
      setFormValues(blankValues(name, table));
      await reloadRows(name, pageNumber);
    } catch (err) {
      if (tableNameRef.current === name) {
        setError(err.message);
        setMessage("");
      }
    }
  }

  // Load the current row before filling the form, so the edit is not based on the list copy.
  async function onEdit(id) {
    const name = tableName;
    try {
      const row = await api("/api/" + name + "/" + id);
      if (tableNameRef.current !== name) return;
      setEditingId(row[table.primary_key]);
      setFormValues(valuesFromRow(name, table, row));
      setError("");
    } catch (err) {
      if (tableNameRef.current === name) setError(err.message);
    }
  }

  // Ask before deleting. A foreign key may still refuse the delete.
  async function onDelete(id) {
    if (!window.confirm("Delete " + tableName + " " + id + "?")) return;
    const name = tableName;
    const pageNumber = page;
    try {
      await api("/api/" + name + "/" + id, { method: "DELETE" });
      if (tableNameRef.current !== name) return;
      if (String(editingId) === String(id)) {
        setEditingId(null);
        setFormValues(blankValues(name, table));
      }
      setError("");
      setMessage("Deleted " + name + " " + id + ".");
      await reloadRows(name, pageNumber);
    } catch (err) {
      if (tableNameRef.current === name) {
        setError(err.message);
        setMessage("");
      }
    }
  }

  // Leave edit mode and clear the form back to an add form.
  function cancelEdit() {
    setEditingId(null);
    setFormValues(blankValues(tableName, table));
  }

  if (!table) {
    return <p className={error ? "error" : ""}>{error || "Loading tables..."}</p>;
  }

  const columns = table.columns.filter((column) => column !== table.primary_key);
  const rowsReady = loaded.table === tableName && loaded.page === page;
  const rows = rowsReady ? loaded.rows : [];
  const total = rowsReady ? loaded.total : 0;
  const firstRow = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const lastRow = Math.min((page + 1) * PAGE_SIZE, total);
  const hasNext = rowsReady && (page + 1) * PAGE_SIZE < total;

  return (
    <>
      <h1>CRUD Test</h1>
      <p>
        This is a test of the CRUD functionality to test the database.
        This is also using the React framework to create the interface.
      </p>

      <label>
        Table:
        <select value={tableName} onChange={onTableChange}>
          {orderedNames(tables).map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
      </label>

      {message && <p className="message" role="status">{message}</p>}
      {error && <p className="error" role="alert">{error}</p>}

      <h2>{editingId == null ? "Add " + tableName : "Edit " + tableName}</h2>
      <form onSubmit={onSubmit} autoComplete="off">
        {columns.map((column) => {
          const choices = (ENUMS[tableName] || {})[column];
          const fk = (FOREIGN_KEYS[tableName] || {})[column];
          const isBool = (BOOLEANS[tableName] || []).includes(column);

          // A column is a checkbox, a fixed choice, a related row, or plain text.
          if (isBool) {
            return (
              <label key={column}>
                {column}
                <input
                  type="checkbox"
                  checked={formValues[column] === true}
                  onChange={(event) => setField(column, event.target.checked)}
                />
              </label>
            );
          }

          if (choices) {
            return (
              <label key={column}>
                {column}
                <select
                  value={formValues[column] ?? ""}
                  onChange={(event) => setField(column, event.target.value)}
                >
                  {choices.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
            );
          }

          if (fk) {
            return (
              <ForeignKeyPicker
                key={column}
                column={column}
                parentTable={fk.table}
                parentKey={tables[fk.table].primary_key}
                labelColumn={fk.label}
                value={formValues[column] ?? ""}
                optional={(OPTIONAL_FK[tableName] || []).includes(column)}
                onChange={(value) => setField(column, value)}
              />
            );
          }

          return (
            <label key={column}>
              {column}
              <input
                value={formValues[column] ?? ""}
                onChange={(event) => setField(column, event.target.value)}
              />
            </label>
          );
        })}

        <button type="submit">{editingId == null ? "Add Row" : "Save Changes"}</button>
        {editingId != null && (
          <button type="button" className="secondary" onClick={cancelEdit}>cancel</button>
        )}
      </form>

      <h2>Rows</h2>
      <div className="pager">
        <span>{rowsReady ? "Rows " + firstRow + "–" + lastRow + " of " + total : "Loading rows..."}</span>
        <div className="pager-buttons">
          <button
            type="button"
            className="secondary"
            onClick={() => changePage(page - 1)}
            disabled={!rowsReady || page === 0}
          >
            Previous
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => changePage(page + 1)}
            disabled={!hasNext}
          >
            Next
          </button>
        </div>
      </div>

      <div className="rows">
        {rowsReady && rows.length === 0 ? <p>No rows in {tableName}.</p> : rowsReady && (
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
                    // Show each cell as text. Objects are written out so they stay readable.
                    const text = value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
                    return <td key={column}>{text}</td>;
                  })}
                  <td>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => onEdit(row[table.primary_key])}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => onDelete(row[table.primary_key])}
                    >
                      Delete
                    </button>
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
