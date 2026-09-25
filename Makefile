.PHONY: test run-server11 run-bserve

test:
	python3 -m unittest discover -s tests -v

run-server11:
	python3 src/http11/server11.py 8080

run-bserve:
	python3 src/server/bserve.py ./www 9000
