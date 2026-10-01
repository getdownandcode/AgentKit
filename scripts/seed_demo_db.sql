-- AgentKit Demo Database Seed Script
-- Populates demo products and orders tables for multi-tool agent demonstrations

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    total_price DECIMAL(10, 2) NOT NULL,
    order_date DATE NOT NULL,
    customer_name VARCHAR(100) NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id)
);

-- Seed Products
INSERT INTO products (id, name, category, price, stock) VALUES
    (1, 'Pro Laptop 15"', 'Electronics', 1200.00, 25),
    (2, 'Wireless Noise-Canceling Headphones', 'Electronics', 250.00, 50),
    (3, 'Mechanical Keyboard', 'Electronics', 150.00, 80),
    (4, 'Ergonomic Desk Chair', 'Furniture', 350.00, 15),
    (5, 'Standing Desk Converter', 'Furniture', 280.00, 20),
    (6, 'Designing Data-Intensive Applications', 'Books', 45.00, 100),
    (7, 'Site Reliability Engineering', 'Books', 40.00, 120)
ON CONFLICT (id) DO NOTHING;

-- Seed Orders (Electronics total in 2024 = $2,400 + $500 + $300 = $3,200.00)
INSERT INTO orders (id, product_id, quantity, total_price, order_date, customer_name) VALUES
    (1, 1, 2, 2400.00, '2024-03-15', 'Alice Chen'),
    (2, 2, 2, 500.00, '2024-04-10', 'Bob Smith'),
    (3, 3, 2, 300.00, '2024-05-20', 'Charlie Brown'),
    (4, 4, 1, 350.00, '2024-02-11', 'Diana Prince'),
    (5, 6, 2, 90.00, '2024-01-25', 'Evan Wright')
ON CONFLICT (id) DO NOTHING;
