## Proof of Implementation

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

### 1. Test Suite

The full automated test suite runs clean, with every test passing.

![All tests passing](screenshots/01-tests.png)

### 2. HTTP/1.1 Calculator

The HTTP/1.1 server correctly processes calculator requests end to end.

![HTTP calculator](screenshots/02-http-calculator.png)

### 3. Persistent HTTP/1.1 Connection

A single TCP connection is shown handling multiple HTTP requests in sequence,
without being reopened between them.

![Persistent connection](screenshots/03-persistent-connection.png)

### 4. Binary File Server

The BHP/1 server correctly serves files to a connecting client.

![Binary server](screenshots/04-binary-server.png)

### 5. Binary Frame / Hex Output

Running the client in verbose mode reveals the underlying binary frame
structure and the raw response bytes.

![Binary frame](screenshots/05-binary-frame.png)