fn main() {
    let mut sort = false;
    let mut limit: Option<usize> = None;

    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--sort" => sort = true,
            "--limit" => {
                if let Some(value) = args.get(i + 1) {
                    match value.parse::<usize>() {
                        Ok(n) => limit = Some(n),
                        Err(_) => {
                            eprintln!("--limit expects a number, got {value:?}");
                            std::process::exit(2);
                        }
                    }
                    i += 1;
                } else {
                    eprintln!("--limit expects a number");
                    std::process::exit(2);
                }
            }
            other => {
                eprintln!("unknown option: {other}");
                std::process::exit(2);
            }
        }
        i += 1;
    }

    println!("sort: {sort}");
    match limit {
        Some(n) => println!("limit: {n}"),
        None => println!("limit: none"),
    }
}
