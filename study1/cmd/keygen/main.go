package main

import (
	"delegatedfl/study1/internal/ckks"
	"flag"
	"log"
)

func main() {
	output := flag.String("output", "", "new exclusive key-state directory on A")
	flag.Parse()
	if *output == "" {
		log.Fatal("--output required")
	}
	a, e := ckks.NewAuthority()
	if e != nil {
		log.Fatal(e)
	}
	if e = a.Save(*output); e != nil {
		log.Fatal(e)
	}
	log.Printf("created Study-I authority key state at %s; only A may retain secret.key", *output)
}
