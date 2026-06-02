from database import get_db, get_cursor

TABLE = 'vessels'

def get_data(page=1, size=20, filters=None):
    conn = get_db()
    cur = get_cursor(conn)
    
    # Build WHERE clause based on filters
    where_clauses = []
    params = []
    
    if filters:
        for f in filters:
            column = f.get('column', '')
            operator = f.get('operator', 'contains')
            value = f.get('value', '')
            
            if not column or not value:
                continue
            
            if operator == 'contains':
                where_clauses.append(f'LOWER(CAST({column} AS TEXT)) LIKE LOWER(%s)')
                params.append(f'%{value}%')
            elif operator == 'equals':
                where_clauses.append(f'{column} = %s')
                params.append(value)
            elif operator == 'gt':
                where_clauses.append(f'{column} > %s')
                params.append(value)
            elif operator == 'lt':
                where_clauses.append(f'{column} < %s')
                params.append(value)
    
    where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'
    
    # Get total count
    count_sql = f'SELECT COUNT(*) FROM {TABLE} WHERE {where_sql}'
    cur.execute(count_sql, params)
    total = cur.fetchone()['count']
    
    # Get paginated data
    data_sql = f'SELECT * FROM {TABLE} WHERE {where_sql} ORDER BY id DESC LIMIT %s OFFSET %s'
    cur.execute(data_sql, params + [size, (page-1)*size])
    rows = cur.fetchall()
    conn.close()
    
    return [dict(r) for r in rows], total

def get_next_doc_num():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f"SELECT MAX(CAST(SUBSTR(doc_num, 3) AS INTEGER)) FROM {TABLE} WHERE doc_num LIKE 'VM%%'")
    result = cur.fetchone()['max']
    conn.close()
    next_num = (result or 0) + 1
    return f"VM{next_num}"

INTEGER_FIELDS = {'gt', 'dwt', 'loa', 'beam', 'no_of_holds', 'num_cranes', 'year_of_built'}

def save_data(data):
    conn = get_db()
    cur = get_cursor(conn)
    row_id = data.get('id')

    # Convert empty strings to None for integer fields
    for f in INTEGER_FIELDS:
        if f in data and data[f] == '':
            data[f] = None

    if row_id:
        # Don't allow changing doc_num on update
        cols = [k for k in data if k != 'id' and k != 'doc_num']
        cur.execute(f"UPDATE {TABLE} SET {', '.join([f'{c}=%s' for c in cols])} WHERE id=%s",
                   [data[c] for c in cols] + [row_id])
    else:
        # Auto-generate doc_num for new entries
        data['doc_num'] = get_next_doc_num()
        cols = [k for k in data if k != 'id']
        cur.execute(f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({', '.join(['%s']*len(cols))}) RETURNING id",
                   [data[c] for c in cols])
        row_id = cur.fetchone()['id']

    conn.commit()
    conn.close()
    return row_id, data.get('doc_num')

def delete_data(row_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f'DELETE FROM {TABLE} WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()
