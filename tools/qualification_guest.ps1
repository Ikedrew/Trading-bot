$ErrorActionPreference='Stop'
# This file is executed manually through the generated .wsb, never on the host.
if ($env:USERNAME -ne 'WDAGUtilityAccount') { throw 'Refusing execution outside the Windows Sandbox account.' }
if (@(Get-NetAdapter | Where-Object Status -eq 'Up').Count -ne 0) { throw 'Active network adapter: isolation preflight failed.' }
if (Test-Path -LiteralPath 'C:\Users\Administrator\Desktop\Trading bot build') { throw 'Production-path exposure detected.' }
$run=Join-Path 'C:\qualification\results' ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
Start-Transcript -LiteralPath (Join-Path $run 'bootstrap.log')
try {
 $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONNOUSERSITE='1'
 $env:OMP_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'
 $env:AWS_EC2_METADATA_DISABLED='true'
 Remove-Item Env:AWS_ACCESS_KEY_ID,Env:AWS_SECRET_ACCESS_KEY,Env:AWS_SESSION_TOKEN,Env:AWS_PROFILE,Env:RESEARCH_AWS_PROFILE -ErrorAction SilentlyContinue
 New-Item -ItemType Directory -Path 'C:\qualification\deps' | Out-Null
 & 'C:\qualification\python\python.exe' -m pip install --no-index --only-binary=:all: --find-links C:\qualification\wheels --target C:\qualification\deps numpy pandas scipy ijson
 if ($LASTEXITCODE -ne 0) { throw 'Offline dependency installation failed; no coordinator launched.' }
 $env:PYTHONPATH='C:\qualification\src;C:\qualification\deps'
 # Job Object keeps evaluator imports and descendants inside one bounded worker.
 Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class QualificationJob {
 [StructLayout(LayoutKind.Sequential)] struct SI { public uint cb; public IntPtr a,b,c; public uint d,e,f,g,h,i,j,k; public ushort l,m; public IntPtr n,o,p,q; }
 [StructLayout(LayoutKind.Sequential)] struct PI { public IntPtr process,thread; public uint pid,tid; }
 [StructLayout(LayoutKind.Sequential)] struct Basic { public long a,b; public uint flags; public UIntPtr min,max; public uint active; public UIntPtr affinity; public uint priority,scheduling; }
 [StructLayout(LayoutKind.Sequential)] struct IO { public ulong a,b,c,d,e,f; }
 [StructLayout(LayoutKind.Sequential)] struct Limits { public Basic basic; public IO io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob; }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcess(string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref SI startup,out PI process);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attr,string name);
 [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job,int type,ref Limits data,uint length);
 [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
 [DllImport("kernel32.dll")] static extern uint ResumeThread(IntPtr thread);
 [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
 [DllImport("kernel32.dll")] static extern bool TerminateJobObject(IntPtr job,uint code);
 [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr process,uint code);
 [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
 public static uint Run(string app,string args,string cwd,ulong memory,uint timeout) {
  IntPtr job=CreateJobObject(IntPtr.Zero,null); PI pi=new PI();
  try {
   Limits limits=new Limits(); limits.basic.flags=0x2000|0x100|0x8; limits.basic.active=1; limits.processMemory=(UIntPtr)memory;
   if(job==IntPtr.Zero || !SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(limits))) throw new Exception("Job limits failed");
   SI si=new SI(); si.cb=(uint)Marshal.SizeOf(si);
   if(!CreateProcess(app,new StringBuilder("\""+app+"\" "+args),IntPtr.Zero,IntPtr.Zero,false,0x08000004,IntPtr.Zero,cwd,ref si,out pi)) throw new Exception("Worker creation failed");
   if(!AssignProcessToJobObject(job,pi.process)) { TerminateProcess(pi.process,125); throw new Exception("Worker confinement failed"); }
   if(ResumeThread(pi.thread)==0xffffffff) throw new Exception("Worker resume failed");
   if(WaitForSingleObject(pi.process,timeout)!=0) { TerminateJobObject(job,124); return 124; }
   uint code; if(!GetExitCodeProcess(pi.process,out code)) throw new Exception("Exit status unavailable"); return code;
  } finally { if(pi.thread!=IntPtr.Zero)CloseHandle(pi.thread); if(pi.process!=IntPtr.Zero)CloseHandle(pi.process); if(job!=IntPtr.Zero)CloseHandle(job); }
 }
}
"@
 # Small qualification first. Representative limits require editing this staged
 # bootstrap AFTER reviewing results, never by changing production controls.
 $arguments='-B C:\qualification\src\tools\qualify_research_pipeline.py --bundle C:\qualification\evidence --run-root "'+$run+'" --max-records 10000 --max-objects 200 --max-bytes 268435456'
 $code=[QualificationJob]::Run('C:\qualification\python\python.exe',$arguments,$run,4294967296,1800000)
 [pscustomobject]@{ExitCode=$code; Run=$run; SandboxNetworking='Disabled'; ProcessLimit=1; MemoryBytes=4294967296; TimeoutSeconds=1800} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $run 'launcher_result.json')
 if($code -ne 0) { Write-Error "Qualification did not pass; exit $code. Inspect preserved logs and results." }
} finally { Stop-Transcript }
